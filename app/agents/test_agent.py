import json
import logging

from app.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOKENS = 12_000

TEST_SYSTEM_PROMPT = """Voce e um engenheiro de qualidade senior especializado em testes GoLang.
Sua funcao e gerar testes automatizados completos para codigo Go existente.

Regras obrigatorias:
- Use Go 1.22+ com o pacote testing da stdlib
- Gere testes unitarios para TODAS as camadas (handler, service, repository)
- Use table-driven tests (padrao Go idiomatico)
- Crie mocks/stubs para interfaces (sem frameworks de mock — use implementacoes manuais)
- Teste cenarios de sucesso E de erro
- Teste edge cases (nil, vazio, limites)
- Use testify/assert apenas se ja estiver nas dependencias, senao use stdlib
- Nomeie testes com padrao Test{Funcao}_{Cenario} (ex: TestCreateUser_Success)
- Inclua subtests com t.Run() para organizacao
- NUNCA gere testes que dependam de banco de dados real ou servicos externos
- Use context.Background() para testes

Formato de resposta OBRIGATORIO — retorne APENAS um JSON valido, sem texto antes ou depois:
{
  "test_files": [
    {
      "path": "internal/service/user_service_test.go",
      "content": "package service\\n\\nimport (...)\\n\\n..."
    }
  ],
  "coverage_estimate": "85%",
  "test_count": 12,
  "scenarios_covered": [
    "CreateUser com dados validos",
    "CreateUser com email duplicado",
    "GetUser com ID inexistente"
  ],
  "notes": "Observacoes sobre a estrategia de teste"
}
"""

FIX_TEST_PROMPT = """Os testes GoLang que voce gerou anteriormente falharam.

=== CODIGO FONTE (implementacao) ===
{source_code}

=== TESTES ANTERIORES ===
{previous_tests}

=== ERROS DE COMPILACAO/EXECUCAO ===
{errors}

Corrija TODOS os testes para que compilem e passem.
Retorne o JSON completo com TODOS os arquivos de teste, no mesmo formato obrigatorio.
"""


class TestAgent(BaseAgent):
    def __init__(self):
        super().__init__("TestAgent", TEST_SYSTEM_PROMPT)

    def generate_tests(
        self,
        dev_output: dict,
        architecture: dict | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> dict:
        """Generate test files for the code produced by DevAgent."""
        parts: list[str] = []

        if architecture:
            parts.append(
                f"=== ARQUITETURA DO PROJETO ===\n"
                f"Test strategy: {architecture.get('test_strategy', 'N/A')}\n"
            )

        parts.append(
            f"Gere testes unitarios completos para o seguinte codigo GoLang:\n\n"
            f"{json.dumps(dev_output, indent=2, ensure_ascii=False)}"
        )

        prompt = "\n".join(parts)
        raw = super().run(prompt, max_tokens)
        return self.extract_json(raw, "TestAgent")

    def fix_tests(
        self,
        dev_output: dict,
        previous_test_output: dict,
        errors: str,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> dict:
        """Fix test files that failed compilation or execution."""
        prompt = FIX_TEST_PROMPT.format(
            source_code=json.dumps(dev_output, indent=2, ensure_ascii=False),
            previous_tests=json.dumps(previous_test_output, indent=2, ensure_ascii=False),
            errors=errors,
        )
        raw = super().run(prompt, max_tokens)
        return self.extract_json(raw, "TestAgent")
