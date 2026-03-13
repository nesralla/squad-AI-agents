import json
import logging
import re

from app.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

# 16k tokens — enough for CRUD tasks with 10+ files
_DEFAULT_MAX_TOKENS = 16_000

DEV_SYSTEM_PROMPT = """Voce e um desenvolvedor backend senior especializado em GoLang.
Sua funcao e implementar codigo de alta qualidade baseado nos requisitos fornecidos.

Regras obrigatorias:
- Use Go 1.22+
- Siga as convencoes idiomaticas de Go (Effective Go)
- Aplique Clean Architecture com separacao clara de camadas
- Use interfaces para desacoplamento entre camadas
- Adicione tratamento de erros adequado (nunca ignore erros)
- Escreva codigo testavel e com injecao de dependencia
- Use context.Context em operacoes I/O e chamadas externas
- Estruture o projeto seguindo: cmd/, internal/, pkg/ quando aplicavel
- SEMPRE inclua um arquivo go.mod valido com versao go 1.22
- Adicione comentarios apenas onde a logica nao for autoevidente
- Mantenha o codigo CONCISO — evite boilerplate desnecessario

Formato de resposta OBRIGATORIO — retorne APENAS um JSON valido, sem texto antes ou depois:
{
  "files": [
    {
      "path": "caminho/relativo/ao/repo/arquivo.go",
      "content": "package main\\n\\n..."
    }
  ],
  "summary": "Descricao objetiva do que foi implementado",
  "dependencies": ["github.com/pacote/exemplo v1.2.3"],
  "notes": "Observacoes importantes sobre a implementacao"
}
"""

FIX_PROMPT_TEMPLATE = """O codigo GoLang que voce gerou anteriormente precisa de correcoes.

=== REQUISITO ORIGINAL ===
{task_description}

=== SEU CODIGO ANTERIOR ===
{previous_code}

=== PROBLEMAS ENCONTRADOS ===
{feedback}

Corrija TODOS os problemas listados acima. Mantenha o que esta funcionando e altere apenas o necessario.
Retorne o JSON completo com TODOS os arquivos (incluindo os que nao mudaram), no mesmo formato obrigatorio.
"""


def _try_repair_json(raw: str) -> dict | None:
    """
    Attempt to repair truncated JSON from LLM output.
    Common case: output was cut off mid-string inside the files array.
    """
    # Find the start of the JSON object
    start = raw.find("{")
    if start == -1:
        return None

    text = raw[start:]

    # Try progressively aggressive truncation repairs
    repairs = [
        text,                          # as-is
        text + '"}]}',                 # close string + array + object
        text + '"}], "summary": "truncated", "dependencies": [], "notes": ""}',
    ]

    for attempt in repairs:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue

    return None


def _extract_json(raw: str) -> dict:
    """Extract a JSON object from LLM output, handling markdown fences and truncation."""
    # Try markdown fenced block first
    json_match = re.search(r"```(?:json)?\s*(\{.+\})\s*```", raw, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try raw JSON object
    obj_match = re.search(r"\{.+\}", raw, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group())
        except json.JSONDecodeError:
            pass

    # Try to repair truncated output
    logger.warning("DevAgent JSON parsing failed, attempting repair...")
    repaired = _try_repair_json(raw)
    if repaired and repaired.get("files"):
        logger.info(f"JSON repair succeeded — {len(repaired['files'])} file(s) recovered.")
        return repaired

    raise ValueError(f"DevAgent returned non-JSON response:\n{raw[:500]}")


class DevAgent(BaseAgent):
    def __init__(self):
        super().__init__("DevAgent", DEV_SYSTEM_PROMPT)

    def generate(
        self,
        task_description: str,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        memory_context: str = "",
    ) -> dict:
        """Initial code generation, optionally enriched with RAG context."""
        if memory_context:
            prompt = (
                f"{memory_context}\n"
                f"=== TAREFA ATUAL ===\n{task_description}"
            )
        else:
            prompt = task_description
        raw = super().run(prompt, max_tokens)
        return _extract_json(raw)

    def fix(
        self,
        task_description: str,
        previous_output: dict,
        feedback: str,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> dict:
        """Re-generate code incorporating review feedback and/or build errors."""
        prompt = FIX_PROMPT_TEMPLATE.format(
            task_description=task_description,
            previous_code=json.dumps(previous_output, indent=2, ensure_ascii=False),
            feedback=feedback,
        )
        raw = super().run(prompt, max_tokens)
        return _extract_json(raw)

    # Keep backwards compatibility
    def run(self, task_description: str, max_tokens: int = _DEFAULT_MAX_TOKENS) -> dict:
        return self.generate(task_description, max_tokens)
