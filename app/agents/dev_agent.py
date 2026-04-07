import json
import logging

from app.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

# Max tokens for code generation. The fix() method sends the full previous code
# + feedback as input, so the response needs enough room for the full re-generation.
_DEFAULT_MAX_TOKENS = 32_000

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
        return self.extract_json(raw, "DevAgent")

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
        return self.extract_json(raw, "DevAgent")

    # Keep backwards compatibility
    def run(self, task_description: str, max_tokens: int = _DEFAULT_MAX_TOKENS) -> dict:
        return self.generate(task_description, max_tokens)
