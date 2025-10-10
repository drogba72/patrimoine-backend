# scheduler_nightly.py
import os, sys, uuid, calendar, argparse
from datetime import date, datetime, timedelta
from sqlalchemy import create_engine, and_, func, text
from sqlalchemy.orm import sessionmaker
from models import Asset, AssetLivret, AssetImmo, ImmoLoan, AssetEvent  # réutilise tes models
from zoneinfo import ZoneInfo

DB_URL = os.environ["DATABASE_URL"]
engine = create_engine(DB_URL, pool_pre_ping=True, future=True)
Session = sessionmaker(bind=engine)

TZ = ZoneInfo("Europe/Paris")

def last_day(y, m): return calendar.monthrange(y, m)[1]

def is_due_today_monthly(day_wanted: int, today: date) -> bool:
    # Jours > fin de mois -> déclenche au dernier jour
    d = min(day_wanted or 1, last_day(today.year, today.month))
    return today.day == d

def is_due_today_frequency(rec_freq: str, day_wanted: int, today: date, anchor: date) -> bool:
    rec = (rec_freq or "").lower()
    if rec not in ("mensuel", "trimestriel", "annuel"):
        return False
    months_delta = (today.year - anchor.year) * 12 + (today.month - anchor.month)
    if rec == "trimestriel" and months_delta % 3 != 0:
        return False
    if rec == "annuel" and months_delta % 12 != 0:
        return False
    due_day = min(day_wanted or 1, last_day(today.year, today.month))  # règle “dernier jour”
    return today.day == due_day


def find_existing(session, asset_id: int, origin: str, value_date: date, extra_match: dict[str, str] | None = None):
    q = session.query(AssetEvent).filter(
        AssetEvent.asset_id == asset_id,
        AssetEvent.status == "posted",
        AssetEvent.value_date == value_date,
        AssetEvent.data['origin'].astext == origin
    )
    for k, v in (extra_match or {}).items():
        q = q.filter(AssetEvent.data[k].astext == str(v))
    return q.first()

def insert_cash_op(session, user_id:int, asset_id:int, value_date:date, amount:float, note:str, category:str, data:dict):
    ev = AssetEvent(
        user_id=user_id, asset_id=asset_id,
        kind="cash_op", status="posted",
        value_date=value_date, amount=amount,
        category=category, note=note, data=data
    )
    session.add(ev)
    return ev

def insert_expense_immo(session, user_id:int, asset_id:int, value_date:date, amount:float, data:dict, note:str="Échéance prêt"):
    ev = AssetEvent(
        user_id=user_id, asset_id=asset_id,
        kind="expense_change", status="posted",
        value_date=value_date, amount=amount,
        category="loan_payment", note=note, data=data
    )
    session.add(ev)
    return ev

def insert_transfer_pair(session, user_id:int, src_asset_id:int, dst_asset_id:int, value_date:date, amount:float, data:dict, note:str):
    gid = str(uuid.uuid4())
    debit = AssetEvent(
        user_id=user_id, asset_id=src_asset_id, target_asset_id=dst_asset_id,
        kind="transfer", status="posted", value_date=value_date,
        amount=-abs(amount), transfer_group_id=gid, category="loan_payment",
        note=note, data=data
    )
    credit = AssetEvent(
        user_id=user_id, asset_id=dst_asset_id, target_asset_id=src_asset_id,
        kind="transfer", status="posted", value_date=value_date,
        amount=abs(amount), transfer_group_id=gid, category="loan_payment",
        note=note, data=data
    )
    session.add_all([debit, credit])
    return debit, credit

def run_for_day(run_date: date):
    s = Session()
    run = {"inserted": 0, "skipped": 0}
    try:
        # --- DCA LIVRETS ---
        rows = (s.query(Asset, AssetLivret)
                .join(AssetLivret, AssetLivret.asset_id == Asset.id)
                .filter(
                    Asset.type == "livret",
                    func.coalesce(AssetLivret.recurring_amount, 0) > 0
                ).all())

        for asset, lv in rows:
            # Chercher la dernière exécution auto_dca pour ancrer la phase
            last_auto = (s.query(AssetEvent)
                        .filter(AssetEvent.asset_id == asset.id,
                                AssetEvent.data['origin'].astext == 'auto_dca')
                        .order_by(AssetEvent.value_date.desc())
                        .first())

            anchor = (last_auto.value_date if last_auto
                    else (asset.created_at.date() if asset.created_at else run_date))

            if is_due_today_frequency(lv.recurring_frequency, lv.recurring_day, run_date, anchor=anchor):
                # idempotent (par jour + asset + origin)
                if find_existing(s, asset.id, "auto_dca", run_date):
                    run["skipped"] += 1
                    continue
                data = {
                    "origin":"auto_dca",
                    "frequency": (lv.recurring_frequency or "").lower(),
                    "expected_day": lv.recurring_day,
                    "period": run_date.strftime("%Y-%m"),
                }
                insert_cash_op(
                    s, asset.user_id, asset.id, run_date,
                    float(lv.recurring_amount), 
                    note=f"DCA automatique Livret ({lv.bank or asset.label})",
                    category="dca",
                    data=data
                )
                run["inserted"] += 1

        # --- ÉCHÉANCES PRETS ---
        loans = (s.query(ImmoLoan, AssetImmo, Asset)
                 .join(AssetImmo, ImmoLoan.immo_id == AssetImmo.id)
                 .join(Asset, AssetImmo.asset_id == Asset.id)
                 .filter(func.coalesce(ImmoLoan.monthly_payment, 0) > 0,
                         ImmoLoan.loan_start_date.isnot(None),
                         func.coalesce(ImmoLoan.loan_duration_months, 0) > 0)
                 .all())

        for loan, immo, asset in loans:
            # échéance du mois ?
            if not is_due_today_monthly(loan.loan_start_date.day, run_date):
                continue

            # période & borne fin
            months_from_start = (run_date.year - loan.loan_start_date.year)*12 + (run_date.month - loan.loan_start_date.month)
            if months_from_start < 0 or months_from_start >= (loan.loan_duration_months or 0):
                continue

            period = run_date.strftime("%Y-%m")
            exists = (s.query(AssetEvent)
                      .filter(AssetEvent.asset_id == asset.id,
                              AssetEvent.status == "posted",
                              AssetEvent.kind.in_(["transfer","expense_change"]),
                              AssetEvent.value_date == run_date,
                              AssetEvent.data['origin'].astext == 'auto_loan',
                              AssetEvent.data['loan_id'].astext == str(loan.id),
                              AssetEvent.data['period'].astext == period)
                      .first())
            if exists:
                run["skipped"] += 1
                continue

            data = {
                "origin":"auto_loan",
                "loan_id": str(loan.id),
                "period": period
            }
            note = f"Échéance prêt immo (#{loan.id})"

            # Transfer si une source est définie
            if getattr(loan, "pay_from_asset_id", None):
                insert_transfer_pair(
                    s, asset.user_id,
                    src_asset_id=loan.pay_from_asset_id,
                    dst_asset_id=asset.id,
                    value_date=run_date,
                    amount=float(loan.monthly_payment),
                    data=data,
                    note=note
                )
                run["inserted"] += 2
            else:
                insert_expense_immo(
                    s, asset.user_id, asset.id, run_date,
                    amount=float(loan.monthly_payment),
                    data=data, note=note
                )
                run["inserted"] += 1

        s.commit()
        return True, run
    except Exception as e:
        s.rollback()
        return False, {"error": str(e), **run}
    finally:
        s.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD (par défaut: aujourd'hui Europe/Paris)")
    args = parser.parse_args()

    today = datetime.now(TZ).date() if not args.date else date.fromisoformat(args.date)

    # 1) ouvrir un run "running" AVANT le traitement
    s = Session()
    run_id = None
    try:
        run_id = s.execute(
            text("""
                INSERT INTO job_runs (job_name, run_date, started_at, state)
                VALUES (:name, :run_date, now(), 'running')
                RETURNING id
            """),
            {"name": JOB_NAME, "run_date": today}
        ).scalar_one()
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

    # 2) exécuter le job
    ok, stats = run_for_day(today)

    # 3) clôturer le run avec le résultat
    s = Session()
    try:
        s.execute(
            text("""
                UPDATE job_runs
                SET finished_at = now(),
                    state        = :state,
                    ok           = :ok,
                    items_inserted = :ins,
                    items_skipped  = :skp,
                    items_failed   = :fld,
                    message        = :msg
                WHERE id = :id
            """),
            {
                "state": "done" if ok else "error",
                "ok": bool(ok),
                "ins": int(stats.get("inserted", 0)),
                "skp": int(stats.get("skipped", 0)),
                "fld": int(stats.get("failed", 0)),
                "msg": str(stats)[:1000],  # petit résumé
                "id": run_id
            }
        )
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

    print(("OK" if ok else "ERROR"), stats)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
