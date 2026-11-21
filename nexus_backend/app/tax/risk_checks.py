"""Pre-filing input-tax risk scan.

The scan classifies facts the caller already collected; it never fetches data,
never guesses a rate and never clears a blocker on its own. A `block` finding
must be resolved with a recorded reason and evidence before a filing can be
prepared (the caller owns the reason store; this module only says what is
missing).
"""

BLOCK = "block"
WARN = "warn"

DEFAULT_RATE_DEVIATION_BP = 500  # 5 percentage points of headline rate


def _finding(code, level, message, **extra):
    return {
        "code": code,
        "level": level,
        "message": message,
        "requires_reason": level == BLOCK,
        **extra,
    }


def assess(
    *,
    invoices=(),
    filing=None,
    duplicate_filing=False,
    rate_deviation_bp=DEFAULT_RATE_DEVIATION_BP,
):
    """Return ``{"blocked": bool, "findings": [...]}`` for one period.

    `invoices` items: kind(output|input), cents (negative = red letter), rate_bp,
    status(valid|void|exempt), certified(bool), transferred_out(bool),
    period(str), deadline_days(int, optional).
    """
    findings = []
    uncertified = 0
    for item in invoices:
        kind = item.get("kind")
        if kind != "input":
            continue
        cents = item.get("cents")
        status = item.get("status", "valid")
        if type(cents) is not int:
            raise ValueError("发票金额必须是整数分")
        red = cents < 0 or status == "void"
        if red and not item.get("transferred_out"):
            findings.append(
                _finding(
                    "input_red_not_transferred",
                    BLOCK,
                    "红字或作废进项尚未做进项税额转出",
                    cents=cents,
                )
            )
            continue
        if item.get("certified") is not True and status == "valid":
            uncertified += abs(cents)
            continue
        deadline = item.get("deadline_days")
        if type(deadline) is int and deadline <= 30:
            findings.append(
                _finding(
                    "input_near_deadline",
                    WARN,
                    f"进项发票距勾选截止仅 {deadline} 天",
                    cents=cents,
                )
            )
    if uncertified:
        findings.append(
            _finding("input_uncertified", WARN, "存在未标记勾选认证的进项发票", cents=uncertified)
        )
    deviation = _rate_deviation(invoices, rate_deviation_bp)
    if deviation is not None:
        findings.append(
            _finding(
                "rate_deviation",
                WARN,
                f"销项与进项加权税率偏离 {deviation / 100:.2f} 个百分点",
                deviation_bp=deviation,
            )
        )
    if duplicate_filing:
        findings.append(
            _finding("duplicate_filing", BLOCK, "同一税种同期间已存在申报记录，需先核对")
        )
    if filing is not None and filing.get("status") == "unknown":
        findings.append(
            _finding(
                "unknown_receipt",
                BLOCK,
                "上次申报回执结果未知，必须先查回回执终态，禁止自动重报",
            )
        )
    return {
        "blocked": any(item["level"] == BLOCK for item in findings),
        "findings": findings,
    }


def _rate_deviation(invoices, limit_bp):
    output = input_ = 0
    output_tax = input_tax = 0
    for item in invoices:
        cents, rate = item.get("cents"), item.get("rate_bp")
        if item.get("status") in {"void", "exempt"} or type(cents) is not int:
            continue
        if type(rate) is not int:
            continue
        tax = cents * rate // 10_000
        if item.get("kind") == "output":
            output += abs(cents)
            output_tax += tax
        elif item.get("kind") == "input" and item.get("certified") is True:
            input_ += abs(cents)
            input_tax += tax
    if not output or not input_:
        return None
    deviation = abs(output_tax * 10_000 // output - input_tax * 10_000 // input_)
    return deviation if deviation > limit_bp else None
