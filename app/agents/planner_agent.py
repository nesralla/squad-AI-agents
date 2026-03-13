import json
import logging
import re

from app.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = """Voce e um arquiteto de software senior especializado em decomposicao de tarefas.
Sua funcao e analisar requisitos complexos e dividir em subtarefas claras e executaveis.

Regras:
- Analise a descricao da tarefa e identifique a complexidade
- Se a tarefa for simples (CRUD basico, endpoint unico), retorne uma unica subtarefa
- Se for complexa (multiplos servicos, integracao, microservicos), decomponha em subtarefas ordenadas
- Cada subtarefa deve ser independente o suficiente para gerar codigo compilavel
- Identifique dependencias entre subtarefas
- Defina a ordem de execucao
- Estime a complexidade de cada subtarefa (low/medium/high)

Formato de resposta OBRIGATORIO — retorne APENAS um JSON valido, sem texto antes ou depois:
{
  "is_complex": false,
  "total_subtasks": 1,
  "subtasks": [
    {
      "id": 1,
      "title": "Titulo conciso da subtarefa",
      "description": "Descricao detalhada do que implementar",
      "depends_on": [],
      "complexity": "low|medium|high",
      "files_hint": ["cmd/main.go", "internal/handler/..."]
    }
  ],
  "architecture_notes": "Notas sobre decisoes arquiteturais relevantes",
  "suggested_patterns": ["clean-architecture", "repository-pattern"],
  "estimated_files": 8
}
"""


def _extract_json(raw: str) -> dict:
    json_match = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", raw, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    obj_match = re.search(r"\{.+\}", raw, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(f"PlannerAgent returned non-JSON response:\n{raw[:500]}")


class PlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__("PlannerAgent", PLANNER_SYSTEM_PROMPT)

    def plan(self, task_description: str, memory_context: str = "", max_tokens: int = 4000) -> dict:
        """Analyze a task and return a decomposition plan."""
        parts: list[str] = []
        if memory_context:
            parts.append(memory_context)
            parts.append("")
        parts.append(
            f"Analise e decomponha a seguinte tarefa em subtarefas executaveis:\n\n"
            f"{task_description}"
        )
        prompt = "\n".join(parts)
        raw = super().run(prompt, max_tokens)
        return _extract_json(raw)
