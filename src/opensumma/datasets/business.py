"""Planning a company's year: every business transaction, before any is recorded.

The plan has exactly the number of transactions asked for, each one journal entry.
Part of the year is a fixed schedule, the same size for every company: opening
balances, rent, hosting, software, inventory replenishment, insurance and its
amortization, legal fees with their month-end accrual and reversal, payroll with
its funding and tax remittance, depreciation, and bank fees. The rest is the
variable business, which fills the remaining count exactly: sales and their
collections and refunds, one-off vendor bills and their payments, equipment
purchases, card expenses, and adjustments.

Sales are planned first. The fixed costs, such as payroll and rent, and the opening
balances are then sized as shares of the gross profit those sales earn, as a company
staffs to what it earns, so every size of company, with whatever mix of customers
and products its seed gives it, keeps a plausible margin and never runs out of cash
or inventory.

Each concern draws from its own random stream (see ``rng``), so the plan depends
only on the seed, the year, and the number of transactions.
"""

import calendar
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from opensumma.datasets import cast
from opensumma.datasets.cast import Customer, Merchant, Vendor
from opensumma.datasets.model import (
    OPERATING_ACCOUNT,
    BankLine,
    CounterpartyEntry,
    Document,
    Line,
    Plan,
    Transaction,
    dims,
)
from opensumma.datasets.rng import Rng
from opensumma.objects import (
    DEFAULT_COUNTERPARTIES,
    AccountingObjectStatus,
    AccountingObjectType,
    CounterpartyKind,
)

DEFAULT_YEAR = 2026
# The fixed schedule's size, the same for every company.
FIXED_TRANSACTIONS = 237
MIN_TRANSACTIONS = 300
MAX_TRANSACTIONS = 500_000

AR, AP = "1120", "2110"
BANK_SOURCE = "bank_feed"
BANK_ACCOUNT = "Operating ****4417"

# Fixed costs and opening balances as shares of the year's planned gross profit.
_PAYROLL_SHARE = 0.52
_RENT_SHARE = 0.05
_HOSTING_SHARE = 0.03
_SOFTWARE_SHARE = 0.016
_LEGAL_SHARE = 0.022
_INSURANCE_SHARE = 0.01
_OPENING_SHARES = {"cash": 0.45, "equipment": 0.08, "depreciated": 0.03}
_PAID_IN_SHARE = 0.40
# Each department's share of payroll, and its average annual salary in cents.
_DEPARTMENTS = {
    "ENG": (0.54, 13_200_000),
    "GA": (0.12, 8_400_000),
    "MKT": (0.12, 8_800_000),
    "SALES": (0.22, 9_600_000),
}
_EMPLOYER_TAX = 765  # basis points of gross pay
_WITHHOLDING = 2_200

# Opaque, unique numbers for documents: an affine map on 0..10**digits - 1 with a
# multiplier coprime to 10 is a bijection, so distinct keys never collide, and the
# numbers do not reveal the order in which documents were planned.
_SCRAMBLE = {
    "BNK": (7_919, 104_729),
    "PAY": (6_133, 55_441),
    "RCT": (4_663, 88_211),
    "BILL": (2_909, 31_337),
    "RFD": (5_273, 71_761),
}


def reference(kind: str, number: int, digits: int = 7) -> str:
    multiplier, offset = _SCRAMBLE[kind]
    return f"{(number * multiplier + offset) % 10**digits:0{digits}d}"


def key(number: int) -> str:
    return f"T{number:06d}"


def money(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


def text(cents: int) -> str:
    """An amount as business data holds it: a string."""
    return str(money(cents))


def cents(amount: Decimal) -> int:
    return int(amount * 100)


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def next_business_day(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def previous_business_day(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def month_name(year: int, month: int) -> str:
    return f"{calendar.month_name[month]} {year}"


def period_code(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def plan_business(*, transactions: int, seed: int, year: int = DEFAULT_YEAR) -> Plan:
    """A year of business for a company, with exactly ``transactions`` of them."""
    if not MIN_TRANSACTIONS <= transactions <= MAX_TRANSACTIONS:
        raise ValueError(
            f"a dataset has {MIN_TRANSACTIONS} to {MAX_TRANSACTIONS} transactions, "
            f"not {transactions}"
        )
    if not 1900 < year < 9999:
        raise ValueError(f"{year} is not a plausible year")
    return _Planner(seed=seed, year=year, transactions=transactions).plan()


class _Planner:
    def __init__(self, *, seed: int, year: int, transactions: int) -> None:
        self.seed = seed
        self.year = year
        self.target = transactions
        self.variable = transactions - FIXED_TRANSACTIONS
        self.revenue = 0  # the year's planned invoicing, in cents
        self.year_end = date(year, 12, 31)
        self.days = [
            day
            for day in (date(year, 1, 1) + timedelta(n) for n in range(366))
            if day.year == year and day.weekday() < 5
        ]
        self.transactions: list[Transaction] = []
        self.customers = cast.customers(
            Rng(seed, "customers"),
            min(max(round(transactions / 150), 5), cast.MAX_CUSTOMERS),
        )
        self.cogs = dict.fromkeys(range(1, 13), 0)  # cents, by month
        self.equipment: list[tuple[date, int]] = []  # purchase date, cost in cents
        self.opening_depreciation = 0

    # --- The plan ------------------------------------------------------------------

    def plan(self) -> Plan:
        self._variable_business()
        self._opening_balances()
        self._recurring()
        self._bank_fees()
        if len(self.transactions) != self.target:
            raise AssertionError(
                f"planned {len(self.transactions)} transactions, not {self.target}"
            )
        new_customers = [
            customer
            for customer in self.customers
            if not any(spec.code == customer.code for spec in DEFAULT_COUNTERPARTIES)
        ]
        return Plan(
            year=self.year,
            counterparties=(
                *(
                    CounterpartyEntry(v.code, v.name, CounterpartyKind.VENDOR)
                    for v in cast.NEW_VENDORS
                ),
                *(
                    CounterpartyEntry(c.code, c.name, CounterpartyKind.CUSTOMER)
                    for c in new_customers
                ),
            ),
            transactions=tuple(
                sorted(self.transactions, key=lambda t: (t.entry_date, t.key))
            ),
        )

    def _add(
        self,
        kind: str,
        entry_date: date,
        description: str,
        lines: Sequence[Line],
        *,
        document: Document | None = None,
        bank: tuple[int, str] | None = None,
        bank_date: date | None = None,
        reverses: str | None = None,
        number: int | None = None,
    ) -> Transaction:
        """Plan a transaction. ``bank`` is the signed amount in cents and the
        description of its statement line, which posts on ``bank_date``. Its key
        is its ``number``, by default the next one."""
        if number is None:
            number = len(self.transactions) + 1
        bank_line = None
        if bank is not None:
            amount, statement = bank
            bank_line = BankLine(
                posted_on=min(bank_date or entry_date, self.year_end),
                amount=money(amount),
                description=statement[:60],
                reference=reference("BNK", number),
            )
        transaction = Transaction(
            key=key(number),
            kind=kind,
            entry_date=entry_date,
            description=description,
            lines=tuple(lines),
            document=document,
            bank_line=bank_line,
            reverses=reverses,
        )
        self.transactions.append(transaction)
        return transaction

    def _at(self, day: date, rng: Rng) -> datetime:
        return datetime(
            day.year,
            day.month,
            day.day,
            rng.integer(8, 17),
            rng.integer(0, 59),
            tzinfo=UTC,
        )

    def _share(self, fraction: float, *, nearest: int = 1) -> int:
        """A share of the year's gross profit in cents, rounded to ``nearest``."""
        gross_profit = self.revenue - sum(self.cogs.values())
        return max(round(gross_profit * fraction / nearest), 1) * nearest

    # --- Opening balances ----------------------------------------------------------

    def _opening_balances(self) -> None:
        cash, equipment, depreciated = (
            self._share(share, nearest=100_000) for share in _OPENING_SHARES.values()
        )
        # Each month's usage is replenished the next month, so stock never falls
        # more than one month's usage below its opening level.
        inventory = -(-2 * max(self.cogs.values()) // 100_000) * 100_000
        stock = 1_000_000
        paid_in = self._share(_PAID_IN_SHARE, nearest=100_000)
        retained = cash + inventory + equipment - depreciated - stock - paid_in
        self.opening_depreciation = equipment // 48
        self._add(
            "opening_balances",
            date(self.year, 1, 1),
            f"Opening balances at 1 January {self.year}",
            [
                Line(OPERATING_ACCOUNT, debit=money(cash)),
                Line("1140", debit=money(inventory)),
                Line("1510", debit=money(equipment)),
                Line("1590", credit=money(depreciated)),
                Line("3100", credit=money(stock)),
                Line("3200", credit=money(paid_in)),
                Line("3900", credit=money(retained)),
            ],
            number=0,  # recorded first, though planned once sales are known
        )

    # --- The variable business ------------------------------------------------------

    def _variable_business(self) -> None:
        budget = self.variable
        used = self._sales(round(budget * 0.29), max(1, round(budget * 0.012)))
        used += self._occasional_bills(round(budget * 0.16))
        used += self._equipment(max(1, round(budget * 0.004)))
        used += self._adjustments(max(1, round(budget * 0.01)))
        cards = budget - used
        if cards < 1:
            raise AssertionError(f"no room left for card expenses ({cards})")
        self._card_expenses(cards)

    def _sales(self, invoices: int, refunds: int) -> int:
        rng = Rng(self.seed, "sales")
        before = len(self.transactions)
        dates = sorted(rng.choice(self.days) for _ in range(invoices))
        weights = [customer.size for customer in self.customers]
        paid: list[tuple[Transaction, Customer, date]] = []
        for number, day in enumerate(dates, start=1):
            customer = rng.weighted(self.customers, weights)
            invoice = self._invoice(
                rng, customer, day, f"INV-{self.year % 100:02d}-{number:05d}"
            )
            due = day + timedelta(days=30)
            settle = due + timedelta(days=customer.days_late + rng.integer(-10, 10))
            pay_day = next_business_day(max(settle, day + timedelta(days=7)))
            if pay_day <= self.year_end:
                paid.append((invoice, customer, pay_day))
        for invoice, customer, pay_day in paid:
            self._customer_payment(rng, invoice, customer, pay_day)
        candidates = [
            (invoice, customer, pay_day)
            for invoice, customer, pay_day in paid
            if pay_day + timedelta(days=45) <= self.year_end
        ]
        for invoice, customer, pay_day in sorted(
            rng.sample(candidates, min(refunds, len(candidates))),
            key=lambda chosen: chosen[0].key,
        ):
            self._refund(rng, invoice, customer, pay_day)
        return len(self.transactions) - before

    def _invoice(
        self, rng: Rng, customer: Customer, day: date, invoice_number: str
    ) -> Transaction:
        location = customer.location
        if rng.chance(customer.product_share):
            product = rng.choice(cast.PRODUCTS)
            quantity = rng.skewed(
                *{1: (1, 12), 2: (5, 40), 3: (20, 120)}[customer.size]
            )
            amount, cost = quantity * product.price, quantity * product.cost
            self.cogs[day.month] += cost
            product_dims = dims(CLASS="PRODUCT", LOCATION=location)
            lines = [
                Line(AR, debit=money(amount)),
                Line(
                    "4100",
                    credit=money(amount),
                    dimensions=dims(
                        CLASS="PRODUCT", DEPARTMENT="SALES", LOCATION=location
                    ),
                ),
                Line("5100", debit=money(cost), dimensions=product_dims),
                Line("1140", credit=money(cost)),
            ]
            line_class, description = "PRODUCT", f"{quantity} x {product.name}"
            quantity_text, unit_price = str(quantity), text(product.price)
        else:
            service = rng.choice(cast.SERVICES)
            half_hours = rng.skewed(
                *{1: (8, 80), 2: (40, 320), 3: (160, 800)}[customer.size]
            )
            amount = half_hours * service.rate // 2
            hours = f"{half_hours // 2}" + (".5" if half_hours % 2 else "")
            lines = [
                Line(AR, debit=money(amount)),
                Line(
                    "4200",
                    credit=money(amount),
                    dimensions=dims(
                        CLASS="SERVICES", DEPARTMENT="SALES", LOCATION=location
                    ),
                ),
            ]
            line_class, description = "SERVICES", f"{hours} hours of {service.name}"
            quantity_text, unit_price = hours, text(service.rate)
        self.revenue += amount
        document = Document(
            object_type=AccountingObjectType.CUSTOMER_INVOICE,
            occurred_at=self._at(day, rng),
            source="billing_system",
            counterparty=customer.code,
            data={
                "invoice_number": invoice_number,
                "customer_name": customer.name,
                "invoice_date": day.isoformat(),
                "due_date": (day + timedelta(days=30)).isoformat(),
                "terms": "Net 30",
                "description": description,
                "quantity": quantity_text,
                "unit_price": unit_price,
                "amount": text(amount),
                "class": line_class,
                "department": "SALES",
                "location": location,
            },
        )
        return self._add(
            "sale",
            day,
            f"Invoice {invoice_number} - {customer.name}",
            lines,
            document=document,
        )

    def _customer_payment(
        self, rng: Rng, invoice: Transaction, customer: Customer, day: date
    ) -> None:
        assert invoice.document is not None
        invoice_number = invoice.document.data["invoice_number"]
        amount = cents(invoice.lines[0].debit)  # the receivable
        method = rng.weighted(("ACH", "check", "wire"), (6, 3, 1))
        number = len(self.transactions) + 1
        statement = {
            "ACH": f"ACH CREDIT {customer.name.upper()}",
            "check": f"REMOTE DEPOSIT CHECK {rng.integer(1000, 99999)}",
            "wire": f"WIRE IN {customer.name.upper()}",
        }[method]
        self._add(
            "customer_payment",
            day,
            f"Payment received - {customer.name} - {invoice_number}",
            [
                Line(OPERATING_ACCOUNT, debit=money(amount)),
                Line(AR, credit=money(amount)),
            ],
            document=Document(
                object_type=AccountingObjectType.CUSTOMER_PAYMENT,
                occurred_at=self._at(day, rng),
                source="accounts_receivable",
                counterparty=customer.code,
                data={
                    "receipt_number": f"RCT-{reference('RCT', number)}",
                    "invoice_number": invoice_number,
                    "customer_name": customer.name,
                    "amount": text(amount),
                    "received_on": day.isoformat(),
                    "method": method,
                },
            ),
            bank=(amount, statement),
            bank_date=next_business_day(day + timedelta(days=rng.integer(0, 1))),
        )

    def _refund(
        self, rng: Rng, invoice: Transaction, customer: Customer, paid_on: date
    ) -> None:
        assert invoice.document is not None
        data = invoice.document.data
        day = next_business_day(paid_on + timedelta(days=rng.integer(5, 40)))
        amount = max(
            cents(invoice.lines[0].debit) * rng.choice((5, 10, 15, 20, 25)) // 100, 1
        )
        number = len(self.transactions) + 1
        line_dims = dims(
            CLASS=data["class"], DEPARTMENT="SALES", LOCATION=data["location"]
        )
        self._add(
            "refund",
            day,
            f"Refund - {customer.name} - {data['invoice_number']}",
            [
                Line("4900", debit=money(amount), dimensions=line_dims),
                Line(OPERATING_ACCOUNT, credit=money(amount)),
            ],
            document=Document(
                object_type=AccountingObjectType.CUSTOMER_PAYMENT,
                occurred_at=self._at(day, rng),
                source="accounts_receivable",
                counterparty=customer.code,
                data={
                    "payment_type": "refund",
                    "refund_number": f"RFD-{reference('RFD', number)}",
                    "invoice_number": data["invoice_number"],
                    "customer_name": customer.name,
                    "amount": text(amount),
                    "refunded_on": day.isoformat(),
                    "reason": rng.choice(
                        (
                            "Service credit for a delayed installation",
                            "Returned hardware",
                            "Billing adjustment",
                        )
                    ),
                },
            ),
            bank=(-amount, f"ACH DEBIT REFUND {customer.name.upper()}"),
            bank_date=next_business_day(day + timedelta(days=rng.integer(0, 1))),
        )

    def _occasional_bills(self, count: int) -> int:
        rng = Rng(self.seed, "bills")
        before = len(self.transactions)
        for day in sorted(rng.choice(self.days) for _ in range(count)):
            vendor = rng.weighted(cast.OCCASIONAL_VENDORS, cast.OCCASIONAL_WEIGHTS)
            department = rng.choice(vendor.departments)
            location = (
                "HQ"
                if department == "GA"
                else rng.weighted(("HQ", "EAST", "WEST"), (6, 2, 2))
            )
            self._bill_and_payment(
                rng,
                vendor,
                day,
                rng.skewed(vendor.low, vendor.high),
                description=_ONE_OFF[vendor.category],
                department=department,
                location=location,
                pay_after=30 + rng.integer(-5, 5),
            )
        return len(self.transactions) - before

    def _equipment(self, count: int) -> int:
        rng = Rng(self.seed, "equipment")
        before = len(self.transactions)
        for day in sorted(rng.choice(self.days) for _ in range(count)):
            amount = rng.skewed(cast.HARDWARE.low, cast.HARDWARE.high)
            self.equipment.append((day, amount))
            self._bill_and_payment(
                rng,
                cast.HARDWARE,
                day,
                amount,
                description=rng.choice(
                    (
                        "Engineering laptops",
                        "Test bench equipment",
                        "Warehouse scanners",
                    )
                ),
                pay_after=30,
            )
        return len(self.transactions) - before

    def _adjustments(self, count: int) -> int:
        rng = Rng(self.seed, "adjustments")
        for _ in range(count):
            month = rng.integer(1, 12)
            day = month_end(self.year, month)
            period = period_code(day)
            if rng.chance(0.5):
                amount = rng.skewed(20_000, 250_000)
                self.cogs[month] += amount
                reason = "Inventory count shortfall"
                lines = [
                    Line(
                        "5100",
                        debit=money(amount),
                        dimensions=dims(CLASS="PRODUCT", LOCATION="HQ"),
                    ),
                    Line("1140", credit=money(amount)),
                ]
                adjustment, description = (
                    "inventory_count",
                    f"Inventory count adjustment - {period}",
                )
            else:
                source, target = rng.sample(("ENG", "GA", "MKT", "SALES"), 2)
                amount = rng.skewed(10_000, 150_000)
                reason = f"Travel charged to {source} belongs to {target}"
                lines = [
                    Line(
                        "6500",
                        debit=money(amount),
                        dimensions=dims(DEPARTMENT=target, LOCATION="HQ"),
                    ),
                    Line(
                        "6500",
                        credit=money(amount),
                        dimensions=dims(DEPARTMENT=source, LOCATION="HQ"),
                    ),
                ]
                adjustment, description = (
                    "reclassification",
                    f"Reclassify travel from {source} to {target} - {period}",
                )
            self._add(
                "adjustment",
                day,
                description,
                lines,
                document=Document(
                    object_type=AccountingObjectType.JOURNAL_ENTRY,
                    occurred_at=self._at(day, rng),
                    source="controller",
                    data={
                        "kind": "adjustment",
                        "adjustment": adjustment,
                        "period": period,
                        "amount": text(amount),
                        "reason": reason,
                    },
                ),
            )
        return count

    def _card_expenses(self, count: int) -> None:
        rng = Rng(self.seed, "cards")
        for day in sorted(rng.choice(self.days) for _ in range(count)):
            merchant = rng.weighted(cast.MERCHANTS, cast.MERCHANT_WEIGHTS)
            self._card_expense(
                rng, merchant, day, rng.skewed(merchant.low, merchant.high)
            )

    def _card_expense(
        self, rng: Rng, merchant: Merchant, day: date, amount: int
    ) -> None:
        department = rng.weighted(("SALES", "MKT", "ENG", "GA"), (4, 2, 2, 1))
        location = rng.weighted(("HQ", "EAST", "WEST"), (5, 3, 2))
        self._add(
            "card_expense",
            day,
            f"Card purchase - {merchant.name}",
            [
                Line(
                    merchant.account,
                    debit=money(amount),
                    dimensions=dims(DEPARTMENT=department, LOCATION=location),
                ),
                Line(OPERATING_ACCOUNT, credit=money(amount)),
            ],
            document=Document(
                object_type=AccountingObjectType.EXPENSE,
                occurred_at=self._at(day, rng),
                source="expense_app",
                data={
                    "merchant": merchant.name,
                    "category": merchant.category,
                    "amount": text(amount),
                    "transaction_date": day.isoformat(),
                    "payment_method": "Company debit card ****4417",
                    "department": department,
                    "location": location,
                },
            ),
            bank=(-amount, f"DEBIT CARD {merchant.name.upper()}"),
            bank_date=next_business_day(day + timedelta(days=rng.integer(0, 2))),
        )

    # --- Bills and payments -----------------------------------------------------------

    def _bill(
        self,
        rng: Rng,
        vendor: Vendor,
        day: date,
        amount: int,
        *,
        description: str,
        department: str | None = None,
        location: str | None = None,
        service_period: str | None = None,
        due: date | None = None,
    ) -> Transaction:
        number = len(self.transactions) + 1
        invoice_number = f"{vendor.invoice_prefix}-{reference('BILL', number, 6)}"
        cost_dims = (
            dims(DEPARTMENT=department, LOCATION=location)
            if department and location
            else ()
        )
        data: dict[str, Any] = {
            "invoice_number": invoice_number,
            "vendor_name": vendor.name,
            "invoice_date": day.isoformat(),
            "due_date": (due or day + timedelta(days=30)).isoformat(),
            "description": description,
            "category": vendor.category,
            "amount": text(amount),
        }
        if service_period is not None:
            data["service_period"] = service_period
        if department and location:
            data["department"], data["location"] = department, location
        return self._add(
            "vendor_bill",
            day,
            f"{vendor.name} - bill {invoice_number}",
            [
                Line(vendor.account, debit=money(amount), dimensions=cost_dims),
                Line(AP, credit=money(amount)),
            ],
            document=Document(
                object_type=AccountingObjectType.VENDOR_BILL,
                occurred_at=self._at(day, rng),
                source=rng.weighted(("email", "vendor_portal", "mail"), (6, 3, 1)),
                counterparty=vendor.code,
                data=data,
            ),
        )

    def _vendor_payment(
        self, rng: Rng, vendor: Vendor, bill: Transaction, day: date
    ) -> None:
        assert bill.document is not None
        invoice_number = bill.document.data["invoice_number"]
        amount = cents(bill.amount)
        number = len(self.transactions) + 1
        self._add(
            "vendor_payment",
            day,
            f"Payment - {vendor.name} - {invoice_number}",
            [
                Line(AP, debit=money(amount)),
                Line(OPERATING_ACCOUNT, credit=money(amount)),
            ],
            document=Document(
                object_type=AccountingObjectType.VENDOR_PAYMENT,
                occurred_at=self._at(day, rng),
                source="accounts_payable",
                counterparty=vendor.code,
                data={
                    "payment_number": f"PAY-{reference('PAY', number)}",
                    "invoice_number": invoice_number,
                    "vendor_name": vendor.name,
                    "amount": text(amount),
                    "paid_on": day.isoformat(),
                    "method": "ACH",
                },
            ),
            bank=(-amount, f"ACH DEBIT {vendor.name.upper()}"),
            bank_date=next_business_day(day + timedelta(days=rng.integer(0, 2))),
        )

    def _bill_and_payment(
        self,
        rng: Rng,
        vendor: Vendor,
        day: date,
        amount: int,
        *,
        description: str,
        pay_after: int,
        department: str | None = None,
        location: str | None = None,
    ) -> None:
        bill = self._bill(
            rng,
            vendor,
            day,
            amount,
            description=description,
            department=department,
            location=location,
        )
        pay_day = next_business_day(day + timedelta(days=pay_after))
        if pay_day <= self.year_end:
            self._vendor_payment(rng, vendor, bill, pay_day)

    # --- The fixed schedule -----------------------------------------------------------

    def _recurring(self) -> None:
        rng = Rng(self.seed, "recurring")
        rent = self._share(_RENT_SHARE / 12, nearest=10_000)
        hosting = self._share(_HOSTING_SHARE / 12, nearest=100)
        licenses = self._share(_SOFTWARE_SHARE / 12, nearest=100)
        premium = self._share(_INSURANCE_SHARE, nearest=10_000)
        remitted: dict[int, int] = {}
        for month in range(1, 13):
            first = date(self.year, month, 1)
            last = month_end(self.year, month)
            period, name = period_code(first), month_name(self.year, month)

            self._monthly_bill(
                rng,
                cast.RENT,
                first,
                rent,
                f"Office rent - {name}",
                period,
                pay_on=3,
                department="GA",
                location="HQ",
            )
            usage = hosting * rng.integer(90, 125) // 100
            self._monthly_bill(
                rng,
                cast.HOSTING,
                next_business_day(date(self.year, month, 2)),
                usage,
                f"Cloud hosting - {name}",
                period,
                pay_on=18,
                department="ENG",
                location="HQ",
            )
            self._monthly_bill(
                rng,
                cast.SOFTWARE,
                next_business_day(date(self.year, month, 5)),
                licenses * rng.integer(98, 104) // 100,
                f"Software subscriptions - {name}",
                period,
                pay_on=20,
                department="ENG",
                location="HQ",
            )
            if month > 1:
                self._monthly_bill(
                    rng,
                    cast.SUPPLIER,
                    next_business_day(date(self.year, month, 3)),
                    max(self.cogs[month - 1], 50_000),
                    f"Inventory replenishment - {month_name(self.year, month - 1)}",
                    period,
                    pay_on=25,
                )
            else:
                self._monthly_bill(
                    rng,
                    cast.INSURANCE,
                    next_business_day(date(self.year, 1, 5)),
                    premium,
                    f"Annual insurance premium {self.year}",
                    str(self.year),
                    pay_on=20,
                )

            amortized = premium // 12 if month < 12 else premium - 11 * (premium // 12)
            self._add(
                "prepaid_amortization",
                last,
                f"Insurance amortization - {name}",
                [
                    Line(
                        "6800",
                        debit=money(amortized),
                        dimensions=dims(DEPARTMENT="GA", LOCATION="HQ"),
                    ),
                    Line("1130", credit=money(amortized)),
                ],
            )
            self._legal(rng, month)
            remitted[month] = self._payroll(
                rng,
                previous_business_day(date(self.year, month, 15)),
                f"{period}-01 to {period}-15",
            )
            remitted[month] += self._payroll(
                rng, previous_business_day(last), f"{period}-16 to {last.isoformat()}"
            )
            if month > 1:
                due = next_business_day(date(self.year, month, 10))
                self._bank_document(
                    "payroll_tax",
                    due,
                    f"Payroll tax remittance - {month_name(self.year, month - 1)}",
                    [
                        Line("2130", debit=money(remitted[month - 1])),
                        Line(OPERATING_ACCOUNT, credit=money(remitted[month - 1])),
                    ],
                    -remitted[month - 1],
                    "EFTPS TAX PAYMENT",
                )
            depreciation = self.opening_depreciation + sum(
                cost // 36 for bought, cost in self.equipment if bought.month < month
            )
            self._add(
                "depreciation",
                last,
                f"Depreciation - {name}",
                [
                    Line(
                        "6600",
                        debit=money(depreciation),
                        dimensions=dims(DEPARTMENT="GA", LOCATION="HQ"),
                    ),
                    Line("1590", credit=money(depreciation)),
                ],
            )

    def _monthly_bill(
        self,
        rng: Rng,
        vendor: Vendor,
        day: date,
        amount: int,
        description: str,
        service_period: str,
        *,
        pay_on: int,
        department: str | None = None,
        location: str | None = None,
    ) -> None:
        bill = self._bill(
            rng,
            vendor,
            day,
            amount,
            description=description,
            department=department,
            location=location,
            service_period=service_period,
            due=date(day.year, day.month, pay_on),
        )
        self._vendor_payment(
            rng, vendor, bill, next_business_day(date(day.year, day.month, pay_on))
        )

    def _legal(self, rng: Rng, month: int) -> None:
        """The month's legal fees: accrued at month end, the accrual reversed on the
        first of the next month, when the bill for them arrives."""
        name = month_name(self.year, month)
        fees = self._share(_LEGAL_SHARE / 12) * rng.integer(80, 130) // 100
        estimate = max(round(fees / 10_000) * 10_000, 10_000)
        last = month_end(self.year, month)
        cost_dims = dims(DEPARTMENT="GA", LOCATION="HQ")
        accrual = self._add(
            "accrual",
            last,
            f"Accrued legal fees - {name}",
            [
                Line(cast.LEGAL.account, debit=money(estimate), dimensions=cost_dims),
                Line("2120", credit=money(estimate)),
            ],
            document=Document(
                object_type=AccountingObjectType.JOURNAL_ENTRY,
                occurred_at=self._at(last, rng),
                source="controller",
                data={
                    "kind": "accrual",
                    "vendor_name": cast.LEGAL.name,
                    "service_period": period_code(last),
                    "estimate": text(estimate),
                    "basis": "Monthly retainer and hours reported by the firm",
                },
            ),
        )
        if month == 12:
            return
        first = date(self.year, month + 1, 1)
        self._add(
            "accrual_reversal",
            first,
            f"Reversal of accrued legal fees - {name}",
            [
                Line(
                    line.account,
                    debit=line.credit,
                    credit=line.debit,
                    dimensions=line.dimensions,
                )
                for line in accrual.lines
            ],
            reverses=accrual.key,
        )
        self._monthly_bill(
            rng,
            cast.LEGAL,
            next_business_day(date(self.year, month + 1, 8)),
            fees,
            f"Legal services - {name}",
            period_code(last),
            pay_on=26,
            department="GA",
            location="HQ",
        )

    def _payroll(self, rng: Rng, pay_day: date, pay_period: str) -> int:
        """One payroll and the transfer that funds it; returns the taxes it owes."""
        lines: list[Line] = []
        gross_total = withheld_total = taxes_total = 0
        headcount = 0
        for department, (share, salary) in _DEPARTMENTS.items():
            annual = self._share(_PAYROLL_SHARE * share)
            headcount += max(1, round(annual / salary))
            gross = annual // 24
            taxes = gross * _EMPLOYER_TAX // 10_000
            withheld = gross * _WITHHOLDING // 10_000
            cost_dims = dims(DEPARTMENT=department, LOCATION="HQ")
            lines.append(Line("6300", debit=money(gross), dimensions=cost_dims))
            lines.append(Line("6310", debit=money(taxes), dimensions=cost_dims))
            gross_total += gross
            withheld_total += withheld
            taxes_total += taxes
        net = gross_total - withheld_total
        owed = withheld_total + taxes_total
        lines += [Line("1112", credit=money(net)), Line("2130", credit=money(owed))]
        self._bank_document(
            "payroll_funding",
            previous_business_day(pay_day - timedelta(days=1)),
            f"Fund payroll account - {pay_day.isoformat()}",
            [
                Line("1112", debit=money(net)),
                Line(OPERATING_ACCOUNT, credit=money(net)),
            ],
            -net,
            "TRANSFER TO PAYROLL ****2291",
        )
        self._add(
            "payroll",
            pay_day,
            f"Payroll - {pay_day.isoformat()}",
            lines,
            document=Document(
                object_type=AccountingObjectType.JOURNAL_ENTRY,
                occurred_at=self._at(pay_day, rng),
                source="payroll_provider",
                data={
                    "kind": "payroll",
                    "pay_date": pay_day.isoformat(),
                    "pay_period": pay_period,
                    "headcount": headcount,
                    "gross_pay": text(gross_total),
                    "employee_withholding": text(withheld_total),
                    "employer_taxes": text(taxes_total),
                    "net_pay": text(net),
                },
            ),
        )
        return owed

    def _bank_document(
        self,
        kind: str,
        day: date,
        description: str,
        lines: Sequence[Line],
        amount: int,
        statement: str,
    ) -> None:
        """A transaction whose document is the bank statement line itself."""
        number = len(self.transactions) + 1
        self._add(
            kind,
            day,
            description,
            lines,
            document=bank_document(
                BankLine(day, money(amount), statement, reference("BNK", number))
            ),
        )

    def _bank_fees(self) -> None:
        """Each month's account fee: a base charge and a charge per statement line."""
        lines_by_month = dict.fromkeys(range(1, 13), 0)
        for transaction in self.transactions:
            posted = statement_date(transaction)
            if posted is not None:
                lines_by_month[posted.month] += 1
        for month in range(1, 13):
            day = previous_business_day(month_end(self.year, month))
            fee = 2_500 + 20 * lines_by_month[month]
            self._bank_document(
                "bank_fee",
                day,
                f"Bank service charges - {month_name(self.year, month)}",
                [
                    Line(
                        "6900",
                        debit=money(fee),
                        dimensions=dims(DEPARTMENT="GA", LOCATION="HQ"),
                    ),
                    Line(OPERATING_ACCOUNT, credit=money(fee)),
                ],
                -fee,
                "ACCOUNT ANALYSIS FEE",
            )


_ONE_OFF = {
    "office_supplies": "Office supplies order",
    "software_subscription": "Additional user licenses",
    "hosting": "Additional compute capacity",
    "legal": "Contract review",
    "travel": "Team travel booking",
    "consulting": "Operations advisory engagement",
    "design": "Brand and product design sprint",
}


def bank_document(
    line: BankLine,
    status: AccountingObjectStatus = AccountingObjectStatus.CLASSIFIED,
) -> Document:
    """A bank statement line as the accounting object the bank feed delivers, early
    on the day it posts."""
    posted = line.posted_on
    return Document(
        object_type=AccountingObjectType.BANK_TRANSACTION,
        occurred_at=datetime(posted.year, posted.month, posted.day, 6, tzinfo=UTC),
        source=BANK_SOURCE,
        status=status,
        data={
            "bank_account": BANK_ACCOUNT,
            "posted_on": line.posted_on.isoformat(),
            "amount": str(line.amount),
            "description": line.description,
            "reference": line.reference,
        },
    )


def statement_date(transaction: Transaction) -> date | None:
    """The day a transaction's money moves on the bank statement, if it does."""
    if transaction.bank_line is not None:
        return transaction.bank_line.posted_on
    document = transaction.document
    if (
        document is not None
        and document.object_type is AccountingObjectType.BANK_TRANSACTION
    ):
        return date.fromisoformat(document.data["posted_on"])
    return None
