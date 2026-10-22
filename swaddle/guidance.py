"""Published pediatric reference points Swaddle compares a baby's log against.

Swaddle never diagnoses. It compares logged facts with a small set of widely
published thresholds and, when one is crossed, says so plainly and tells the
caregiver to call their pediatrician. Every threshold carries its source so the
assistant can cite it out loud.
"""

from __future__ import annotations

from dataclasses import dataclass

AAP_FEVER_URL = "https://www.healthychildren.org/English/health-issues/conditions/fever/Pages/When-to-Call-the-Pediatrician.aspx"
AAP_ENOUGH_MILK_URL = "https://www.healthychildren.org/English/ages-stages/baby/breastfeeding/Pages/How-to-Tell-if-Baby-is-Getting-Enough-Milk.aspx"

DISCLAIMER = "Swaddle is a logging assistant, not medical advice. If you are worried, call your pediatrician."


@dataclass(frozen=True)
class Reference:
    key: str
    statement: str
    source: str
    url: str


REFERENCES: dict[str, Reference] = {
    "fever_under_3_months": Reference(
        key="fever_under_3_months",
        statement="Call the pediatrician if a baby younger than 3 months (12 weeks) has a temperature of 100.4°F (38.0°C) or higher.",
        source="American Academy of Pediatrics, HealthyChildren.org",
        url=AAP_FEVER_URL,
    ),
    "fever_over_104": Reference(
        key="fever_over_104",
        statement="At any age, call the pediatrician if a temperature rises above 104°F (40°C) repeatedly.",
        source="American Academy of Pediatrics, HealthyChildren.org",
        url=AAP_FEVER_URL,
    ),
    "fever_duration_under_2": Reference(
        key="fever_duration_under_2",
        statement="Call the pediatrician if a fever lasts more than 24 hours in a child younger than 2.",
        source="American Academy of Pediatrics, HealthyChildren.org",
        url=AAP_FEVER_URL,
    ),
    "wet_diapers": Reference(
        key="wet_diapers",
        statement="By 5 to 7 days old, a baby should have 6 or more wet diapers per day.",
        source="American Academy of Pediatrics, HealthyChildren.org",
        url=AAP_ENOUGH_MILK_URL,
    ),
    "feeds_per_day": Reference(
        key="feeds_per_day",
        statement="A breastfed newborn should nurse at least 8 to 12 times every 24 hours.",
        source="American Academy of Pediatrics, HealthyChildren.org",
        url=AAP_ENOUGH_MILK_URL,
    ),
}

# Typical awake-time ranges (minutes) by age in weeks. These are a prior only:
# Swaddle blends them with the baby's own recent wake windows, and the baby's
# data dominates once there is enough of it.
WAKE_WINDOW_PRIOR: list[tuple[int, int, int]] = [
    # (max_age_weeks, low_min, high_min)
    (4, 35, 60),
    (8, 45, 75),
    (12, 60, 90),
    (16, 75, 120),
    (26, 120, 180),
    (52, 150, 240),
]


def wake_window_prior(age_weeks: float) -> tuple[int, int]:
    for max_age, low, high in WAKE_WINDOW_PRIOR:
        if age_weeks <= max_age:
            return low, high
    return WAKE_WINDOW_PRIOR[-1][1], WAKE_WINDOW_PRIOR[-1][2]


def to_fahrenheit(value: float, unit: str) -> float:
    return value * 9 / 5 + 32 if unit.upper().startswith("C") else value


@dataclass(frozen=True)
class TemperatureTriage:
    level: str  # "ok" | "watch" | "call_now"
    speech: str
    reference: Reference | None


def triage_temperature(temp_f: float, age_weeks: float, fever_hours: float | None = None) -> TemperatureTriage:
    """Compare one reading with the AAP 'when to call' thresholds."""
    temp_c = (temp_f - 32) * 5 / 9
    reading = f"{temp_f:.1f} degrees Fahrenheit ({temp_c:.1f} Celsius)"
    if age_weeks < 12 and temp_f >= 100.4:
        ref = REFERENCES["fever_under_3_months"]
        return TemperatureTriage(
            "call_now",
            f"{reading} is a fever for a baby under 3 months. The American Academy of Pediatrics says to call your pediatrician now, even in the middle of the night.",
            ref,
        )
    if temp_f > 104:
        ref = REFERENCES["fever_over_104"]
        return TemperatureTriage(
            "call_now",
            f"{reading} is above 104. If it stays this high, the American Academy of Pediatrics says to call your pediatrician.",
            ref,
        )
    if fever_hours is not None and fever_hours > 24 and temp_f >= 100.4:
        ref = REFERENCES["fever_duration_under_2"]
        return TemperatureTriage(
            "call_now",
            f"{reading}, and the fever has lasted about {fever_hours:.0f} hours. Guidance says to call your pediatrician when a fever lasts more than 24 hours under age 2.",
            ref,
        )
    if temp_f >= 100.4:
        return TemperatureTriage(
            "watch",
            f"{reading} is a fever. I'll keep track of how long it lasts. Call your pediatrician if your baby seems very sleepy, very irritable, or has trouble breathing.",
            None,
        )
    return TemperatureTriage("ok", f"{reading} is in the normal range. Logged.", None)
