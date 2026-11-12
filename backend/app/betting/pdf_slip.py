from __future__ import annotations
"""
pdf_slip.py – Generate a branded PDF / clean text betting slip for FIE combos.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

EAT_TZ = timezone(timedelta(hours=3), name="EAT")

def _clean_latin(s: Any) -> str:
    if s is None:
        return ""
    text = str(s)
    replacements = {
        "—": "-", "–": "-", "·": "|", "’": "'", "‘": "'",
        "“": '"', "”": '"', "•": "*", "→": "->", "≈": "~",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text.encode("latin-1", "replace").decode("latin-1")

def generate_combo_pdf(
    combo: Dict[str, Any],
    matchday: str,
    bankroll: float = 1000.0,
    currency: str = "USD",
) -> Optional[bytes]:
    """Generate a clean PDF bet slip and return as bytes."""
    try:
        from fpdf import FPDF
    except ImportError:
        return None

    from app.core.currency import format_stake_dual

    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(15, 15, 15)

    # Header bar
    pdf.set_fill_color(13, 17, 23)
    pdf.rect(0, 0, 210, 36, "F")
    pdf.set_text_color(0, 200, 81)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_y(8)
    pdf.cell(0, 10, _clean_latin("FIE Football Intelligence Engine"), ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(170, 170, 170)
    pdf.cell(0, 6, _clean_latin("Quantitative Betting Research Platform | Dixon-Coles Poisson + AI"), ln=True, align="C")

    # Slip title
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(44)
    pdf.set_font("Helvetica", "B", 13)
    n_legs = combo.get("legs_count", len(combo.get("legs", [])))
    pdf.cell(0, 8, _clean_latin(f"DAILY BANKER SLIP - {n_legs}-TEAM ACCUMULATOR"), ln=True)
    pdf.set_font("Helvetica", "", 9)
    eat_now = datetime.now(EAT_TZ).strftime("%a %d %b %Y, %H:%M EAT")
    pdf.cell(0, 5, _clean_latin(f"Generated: {eat_now}  |  Matchday: {matchday}"), ln=True)
    pdf.ln(3)

    # Summary box
    pdf.set_fill_color(240, 248, 240)
    pdf.rect(15, pdf.get_y(), 180, 20, "F")
    pdf.set_x(18)
    total_odds = combo.get("total_odds", 0.0)
    comb_prob = combo.get("combined_probability", 0.0)
    ev = combo.get("expected_value_percent", 0.0)
    kelly_amt = combo.get("kelly_stake_amount", 0.0)
    avg_p = combo.get("avg_leg_probability", 0.0)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(45, 10, _clean_latin(f"TOTAL ODDS: {total_odds:.2f}"), border=0)
    pdf.cell(45, 10, _clean_latin(f"AVG CONF: {avg_p*100:.1f}%"), border=0)
    pdf.cell(45, 10, _clean_latin(f"WIN PROB: {comb_prob*100:.1f}%"), border=0)
    pdf.cell(40, 10, _clean_latin(f"EV: {ev:+.1f}%"), border=0, ln=True)
    pdf.set_x(18)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(90, 8, _clean_latin(f"Quarter-Kelly Stake: {format_stake_dual(kelly_amt, currency)}"), border=0)
    pdf.ln(15)

    # Legs table header
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(30, 60, 30)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(8, 7, "#", border=1, fill=True)
    pdf.cell(66, 7, "Match", border=1, fill=True)
    pdf.cell(56, 7, "Selection", border=1, fill=True)
    pdf.cell(16, 7, "Odds", border=1, fill=True, align="C")
    pdf.cell(18, 7, "Prob", border=1, fill=True, align="C")
    pdf.cell(0, 7, "Kickoff (EAT)", border=1, fill=True, ln=True)

    # Legs rows
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 8)
    for i, leg in enumerate(combo.get("legs", []), 1):
        fill = (i % 2 == 0)
        pdf.set_fill_color(245, 250, 245)
        pdf.cell(8, 7, str(i), border=1, fill=fill)
        pdf.cell(66, 7, _clean_latin(str(leg.get("match", ""))[:32]), border=1, fill=fill)
        pdf.cell(56, 7, _clean_latin(str(leg.get("selection", ""))[:28]), border=1, fill=fill)
        pdf.cell(16, 7, f"{leg.get('odds', 0.0):.2f}", border=1, fill=fill, align="C")
        pdf.cell(18, 7, f"{leg.get('probability', 0.0)*100:.0f}%", border=1, fill=fill, align="C")
        pdf.cell(0, 7, _clean_latin(str(leg.get("kickoff_eat", ""))[:18]), border=1, fill=fill, ln=True)

    pdf.ln(8)
    # Disclaimer
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(
        0, 4,
        _clean_latin("DISCLAIMER: For statistical research and educational purposes only. "
        "FIE outputs are model probabilities, not guarantees. Gamble responsibly with money you can afford to lose. 18+ only."),
        align="C"
    )

    return bytes(pdf.output())

def generate_text_slip(combo: Dict[str, Any], matchday: str, currency: str = "USD") -> str:
    """Generate clean copyable text slip."""
    from app.core.currency import format_stake_dual
    lines = [
        "⚽ FIE DAILY BANKER MULTI-BET SLIP",
        f"📅 Matchday: {matchday}",
        f"⏰ Generated: {datetime.now(EAT_TZ).strftime('%a %d %b %Y, %H:%M EAT')}",
        f"📊 Odds: {combo.get('total_odds', 0.0):.2f} | Win Prob: {combo.get('combined_probability', 0.0)*100:.1f}% | EV: {combo.get('expected_value_percent', 0.0):+.1f}%",
        f"💼 Suggested Kelly Stake: {format_stake_dual(combo.get('kelly_stake_amount', 0.0), currency)}",
        "--------------------------------------------------",
    ]
    for i, leg in enumerate(combo.get("legs", []), 1):
        lines.append(f"{i}. {leg['match']}")
        lines.append(f"   Selection: {leg['selection']} @ {leg['odds']:.2f} (Confidence: {leg['probability']*100:.1f}%)")
        lines.append(f"   Kickoff: {leg.get('kickoff_eat', '')}")
    lines.append("--------------------------------------------------")
    lines.append("⚠️ For research purposes only. Please bet responsibly.")
    return "\n".join(lines)
