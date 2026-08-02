"""Turning a forecast into the two quantities that actually move a baseball.

A temperature and a wind speed are not, by themselves, baseball facts. The two
derived quantities below are, and both are computed from published physics
rather than fitted to outcomes — there is nothing here to overfit and nothing
selected on the games it will be scored against.

**Air density** decides how far a struck ball carries. It is not temperature
alone: a hot, humid, high-altitude evening and a cold, dry, sea-level one can
differ by six or seven percent in density, and humid air is *lighter* than dry
air at the same temperature, which is the opposite of most people's intuition
(water vapour's molar mass is below that of dry air). Coors Field plays the way
it does mostly through this number.

**Field-relative wind** decides whether that carry is with the ball or against
it. A 15 mph wind is a different game blowing from home plate to centre field
than blowing across it, and the compass direction alone cannot tell them apart
without knowing which way the stadium faces.

That second one needs the venue's orientation, and this repository has it for
34 of its ballparks and not for the rest. Where it is missing the component is
**UNAVAILABLE** rather than assumed — a wind treated as neutral because nobody
knows the azimuth is a fabricated zero, and a zero is never a stand-in for an
unknown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Specific gas constants, J/(kg·K).
R_DRY_AIR = 287.058
R_WATER_VAPOUR = 461.495

KELVIN_OFFSET = 273.15
PASCALS_PER_MILLIBAR = 100.0

#: Sea-level dry-air density at 15 °C, the reference a factor is expressed
#: against so a reader has something to compare 1.04 to.
REFERENCE_DENSITY = 1.225

#: Wind below this is not a direction, it is noise. Reporting "out to centre" for
#: a 1 mph breeze would dress a rounding error as a finding.
CALM_MPH = 3.0

#: How far off a straight line still counts as blowing out or in. A 45° cone
#: either side splits the compass into four honest quarters rather than pretending
#: to a precision the forecast does not have.
CONE_DEGREES = 45.0


def saturation_vapour_pressure_pa(temperature_c: float) -> float:
    """Tetens' equation. Pressure at which air of this temperature is saturated."""
    return 610.78 * math.exp((17.27 * temperature_c) / (temperature_c + 237.3))


def air_density_kg_m3(
    temperature_f: float | None,
    pressure_mb: float | None,
    humidity_pct: float | None,
) -> float | None:
    """Density of moist air, from the ideal gas law applied to each component.

        rho = Pd/(Rd*T) + Pv/(Rv*T)

    Humidity is optional and treated as dry air when absent — that is a real
    approximation and it biases density *upward* by up to about half a percent
    on a muggy evening, which is small next to the temperature term and is not
    worth refusing the whole calculation over.
    """
    if temperature_f is None or pressure_mb is None:
        return None
    temperature_c = (temperature_f - 32.0) * 5.0 / 9.0
    kelvin = temperature_c + KELVIN_OFFSET
    if kelvin <= 0:
        return None

    total_pa = pressure_mb * PASCALS_PER_MILLIBAR
    vapour_pa = 0.0
    if humidity_pct is not None:
        vapour_pa = max(
            0.0,
            min(humidity_pct, 100.0) / 100.0 * saturation_vapour_pressure_pa(temperature_c),
        )
    dry_pa = max(total_pa - vapour_pa, 0.0)
    return dry_pa / (R_DRY_AIR * kelvin) + vapour_pa / (R_WATER_VAPOUR * kelvin)


def density_factor(density: float | None) -> float | None:
    """Density against the standard reference. Below 1.0 means the ball carries."""
    if density is None or density <= 0:
        return None
    return density / REFERENCE_DENSITY


@dataclass(frozen=True, slots=True)
class FieldWind:
    """Wind resolved against the way the stadium actually faces."""

    #: Component along home plate → centre field, mph. Positive blows out.
    out_to_centre_mph: float
    #: Component across the field, mph. Positive blows toward right field.
    cross_mph: float
    label: str

    def to_dict(self) -> dict[str, float | str]:
        return {
            "out_to_centre_mph": round(self.out_to_centre_mph, 2),
            "cross_mph": round(self.cross_mph, 2),
            "label": self.label,
        }


def field_relative_wind(
    speed_mph: float | None,
    direction_deg: float | None,
    azimuth_deg: float | None,
) -> FieldWind | None:
    """Resolve a compass wind into out-to-centre and cross-field components.

    ``direction_deg`` follows the meteorological convention: the direction the
    wind is coming *from*. ``azimuth_deg`` is the bearing from home plate toward
    centre field, which is how MLB publishes venue orientation.

    Returns None when the azimuth is unknown. That is the point of the function
    having a None return at all — without the stadium's orientation there is no
    honest answer, and a neutral one would be an invented fact.
    """
    if speed_mph is None or direction_deg is None or azimuth_deg is None:
        return None
    if speed_mph < CALM_MPH:
        return FieldWind(0.0, 0.0, "CALM")

    # Convert "coming from" to "blowing toward", then take the angle between
    # that and the line out to centre field.
    blowing_toward = (direction_deg + 180.0) % 360.0
    offset = math.radians(blowing_toward - azimuth_deg)
    out = speed_mph * math.cos(offset)
    cross = speed_mph * math.sin(offset)

    delta = abs(((blowing_toward - azimuth_deg + 180.0) % 360.0) - 180.0)
    if delta <= CONE_DEGREES:
        label = "OUT_TO_CENTRE"
    elif delta >= 180.0 - CONE_DEGREES:
        label = "IN_FROM_CENTRE"
    elif ((blowing_toward - azimuth_deg) % 360.0) < 180.0:
        label = "LEFT_TO_RIGHT"
    else:
        label = "RIGHT_TO_LEFT"
    return FieldWind(out, cross, label)


def is_enclosed(roof_type: str | None) -> bool:
    """A closed roof makes the outdoor forecast irrelevant to play.

    Retractable roofs are the honest problem here: whether one was shut is a
    fact about the evening that no forecast carries. They are treated as open,
    which is right more often than not, and the uncertainty belongs in the
    feature's estimated flag rather than in a guess dressed as knowledge.
    """
    if not roof_type:
        return False
    lowered = roof_type.strip().lower()
    return "dome" in lowered or lowered in {"closed", "indoor", "fixed"}


__all__ = [
    "CALM_MPH",
    "CONE_DEGREES",
    "REFERENCE_DENSITY",
    "FieldWind",
    "air_density_kg_m3",
    "density_factor",
    "field_relative_wind",
    "is_enclosed",
    "saturation_vapour_pressure_pa",
]
