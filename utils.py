# utils.py
def amortization_monthly_payment(principal: float, annual_rate_percent: float, months: int) -> float:
    if months <= 0:
        return 0.0
    r = annual_rate_percent / 100.0 / 12.0
    if r == 0:
        return principal / months
    payment = principal * (r / (1 - (1 + r) ** (-months)))
    return round(payment, 2)

def amortization_schedule(principal: float, annual_rate_percent: float, months: int):
    r = annual_rate_percent / 100.0 / 12.0
    if months <= 0:
        return []
    if r == 0:
        monthly = principal / months
        schedule = []
        rem = principal
        for m in range(1, months+1):
            rem -= monthly
            schedule.append({'month': m, 'payment': monthly, 'interest': 0.0, 'principal_paid': monthly, 'remaining': max(rem,0)})
        return schedule
    monthly = amortization_monthly_payment(principal, annual_rate_percent, months)
    schedule = []
    rem = principal
    for m in range(1, months+1):
        interest = rem * r
        principal_paid = monthly - interest
        rem -= principal_paid
        schedule.append({'month': m, 'payment': round(monthly,2), 'interest': round(interest,2), 'principal_paid': round(principal_paid,2), 'remaining': round(max(rem,0),2)})
    return schedule
