"""System prompt: short instructions followed by the whole policy manual."""

from emporio.tools import DATA_DIR

INSTRUCTIONS = """\
Você é o assistente virtual da Empório da Música, loja de instrumentos musicais em Campo Grande (MS).
Responda em português do Brasil. Use as ferramentas para preços, estoque, promoções e pedidos.
"""


def build_system_prompt() -> str:
    # Static text only (no dates or session data), so the provider can cache the whole prefix.
    manual = (DATA_DIR / "policies.md").read_text(encoding="utf-8")
    return f"{INSTRUCTIONS}\n# Manual de políticas e procedimentos da loja\n\n<manual>\n{manual}</manual>\n"
