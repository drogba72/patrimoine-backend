# scheduler_nightly.py
import os, sys, uuid, calendar, argparse
from datetime import date, datetime, timedelta
from sqlalchemy import create_engine, and_, func
from sqlalchemy import text as sqltext
from sqlalchemy.orm import sessionmaker
from models import Asset, AssetLivret, AssetImmo, ImmoLoan, AssetEvent  # réutilise tes models
from zoneinfo import ZoneInfo
import json

DB_URL = os.environ["DATABASE_URL"]
engine = create_engine(DB_URL, pool_pre_ping=True, future=True)
Session = sessionmaker(bind=engine)
JOB_NAME = "auto-events"

TZ = ZoneInfo("Europe/Paris")

def last_day(y, m): return calendar.monthrange(y, m)[1]

def add_months_year_month(y: int, m: int, delta: int) -> tuple[int, int]:
    total = (y * 12 + (m - 1)) + delta
    ny, nm = divmod(total, 12)
    return ny, nm + 1  # month 1..12

def due_from_anchor(anchor: date, k_months: int, wanted_day: int) -> date:
    y, m = add_months_year_month(anchor.year, anchor.month, k_months)
    d = min(wanted_day or 1, last_day(y, m))  # règle "dernier jour"
    return date(y, m, d)

def iter_due_dates(rec_freq: str, wanted_day: int, anchor: date, start_date: date, end_date: date):
    """Génère toutes les échéances (incluses) alignées sur 'anchor' et comprises entre start_date et end_date."""
    step = {"mensuel": 1, "trimestriel": 3, "annuel": 12}.get((rec_freq or "").lower())
    if not step:
        return
    # Aligner le premier mois sur la phase ancrée
    months_delta = (start_date.year - anchor.year) * 12 + (start_date.month - anchor.month)
    if months_delta < 0:
        k = 0
    else:
        k = months_delta - (months_delta % step)  # ramener au bloc (mensuel/trimestriel/annuel)
    # Trouver la première échéance >= start_date
    while True:
        d = due_from_anchor(anchor, k, wanted_day)
        if d >= start_date:
            break
        k += step
    # Itérer jusqu'à end_date
    while d <= end_date:
        yield d
        k += step
        d = due_from_anchor(anchor, k, wanted_day)


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

def run_for_day(run_date: date, verbose: bool = False):
    s = Session()
    run = {"inserted": 0, "skipped": 0, "details": []}  # <= détails ajoutés
    try:
        # --- DCA LIVRETS (avec backfill) ---
        rows = (s.query(Asset, AssetLivret)
                .join(AssetLivret, AssetLivret.asset_id == Asset.id)
                .filter(
                    Asset.type == "livret",
                    func.coalesce(AssetLivret.recurring_amount, 0) > 0
                ).all())

        for asset, lv in rows:
            freq = (lv.recurring_frequency or "").lower()
            if freq not in ("mensuel", "trimestriel", "annuel"):
                continue

            # Ancre : dernier auto_dca sinon date de création de l'asset
            last_auto = (s.query(AssetEvent)
                         .filter(AssetEvent.asset_id == asset.id,
                                 AssetEvent.data['origin'].astext == 'auto_dca')
                         .order_by(AssetEvent.value_date.desc())
                         .first())

            anchor = (last_auto.value_date if last_auto
                      else (asset.created_at.date() if asset.created_at else run_date))

            # On backfill de max(anchor, asset.created_at) jusqu'à run_date
            start = max(anchor, (asset.created_at.date() if asset.created_at else anchor))

            for due_dt in iter_due_dates(freq, lv.recurring_day, anchor=anchor,
                                         start_date=start, end_date=run_date):
                # idempotent : existe déjà ?
                existing = find_existing(s, asset.id, "auto_dca", due_dt)
                period = due_dt.strftime("%Y-%m")

                if existing:
                    run["skipped"] += 1
                    run["details"].append({
                        "scope": "livret",
                        "reason": "already_exists_auto_dca",
                        "asset_id": asset.id,
                        "asset_label": asset.label,
                        "value_date": due_dt.isoformat(),
                        "period": period,
                        "event_id": existing.id
                    })
                    continue

                data = {
                    "origin": "auto_dca",
                    "frequency": freq,
                    "expected_day": lv.recurring_day,
                    "period": period,
                }
                insert_cash_op(
                    s, asset.user_id, asset.id, due_dt,
                    float(lv.recurring_amount),
                    note=f"DCA automatique Livret ({lv.bank or asset.label})",
                    category="dca",
                    data=data
                )
                run["inserted"] += 1
                run["details"].append({
                    "scope": "livret",
                    "reason": "inserted_auto_dca",
                    "asset_id": asset.id,
                    "asset_label": asset.label,
                    "value_date": due_dt.isoformat(),
                    "period": period
                })

        # --- ÉCHÉANCES PRETS ---
        loans = (s.query(ImmoLoan, AssetImmo, Asset)
                 .join(AssetImmo, ImmoLoan.immo_id == AssetImmo.id)
                 .join(Asset, AssetImmo.asset_id == Asset.id)
                 .filter(func.coalesce(ImmoLoan.monthly_payment, 0) > 0,
                         ImmoLoan.loan_start_date.isnot(None),
                         func.coalesce(ImmoLoan.loan_duration_months, 0) > 0)
                 .all())

        for loan, immo, asset in loans:
            if not is_due_today_monthly(loan.loan_start_date.day, run_date):
                continue

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
                item = {
                    "scope": "immo_loan",
                    "reason": "already_exists_auto_loan",
                    "asset_id": asset.id,
                    "asset_label": asset.label,
                    "loan_id": loan.id,
                    "value_date": run_date.isoformat(),
                    "period": period,
                    "event_id": exists.id
                }
                run["details"].append(item)
                if verbose:
                    print("SKIP:", item)
                continue

            data = {
                "origin":"auto_loan",
                "loan_id": str(loan.id),
                "period": period
            }
            note = f"Échéance prêt immo (#{loan.id})"

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
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    today = datetime.now(TZ).date() if not args.date else date.fromisoformat(args.date)

    # 1) job_runs: running
    s = Session()
    try:
        run_id = s.execute(
            sqltext("""
                INSERT INTO job_runs (job_name, run_date, started_at, state)
                VALUES (:name, :run_date, now(), 'running')
                RETURNING id
            """),
            {"name": JOB_NAME, "run_date": today}
        ).scalar_one()
        s.commit()
    except:
        s.rollback()
        raise
    finally:
        s.close()

    # 2) run
    ok, stats = run_for_day(today, verbose=args.verbose)

    # 3) job_runs: finalize (message = JSON tronqué)
    s = Session()
    try:
        msg_obj = {"stats": {"inserted": stats.get("inserted", 0),
                             "skipped": stats.get("skipped", 0)},
                   "details": stats.get("details", [])[:20]}  # limite taille
        msg = json.dumps(msg_obj, ensure_ascii=False)[:1000]

        s.execute(
            sqltext("""
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
                "msg": msg,
                "id": run_id
            }
        )
        s.commit()
    except:
        s.rollback()
        raise
    finally:
        s.close()

    if args.verbose:
        for d in stats.get("details", []):
            print("SKIP DETAIL:", d)

    print(("OK" if ok else "ERROR"), stats)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
