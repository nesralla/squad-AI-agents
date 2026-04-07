import json

from app.agents.base_agent import BaseAgent

REVIEWER_SYSTEM_PROMPT = """Você é um code reviewer senior especializado em GoLang.
Sua função é revisar código com rigor técnico e construtivo.

Analise criteriosamente:
1. Segurança — injeção SQL, race conditions, exposição de dados sensíveis
2. Performance — alocações desnecessárias, goroutine leaks, uso inadequado de canais
3. Clareza e legibilidade — nomes expressivos, funções coesas
4. Arquitetura — separação de responsabilidades, acoplamento, coesão
5. Tratamento de erros — erros ignorados, mensagens sem contexto
6. Convenções Go — Effective Go, Go Code Review Comments
7. Testabilidade — dependências injetáveis, funções puras onde possível

Formato de resposta OBRIGATÓRIO — retorne APENAS um JSON válido, sem texto antes ou depois:
{
  "approved": true,
  "score": 8,
  "issues": [
    {
      "severity": "critical|major|minor",
      "file": "caminho/arquivo.go",
      "description": "Descrição clara do problema encontrado",
      "suggestion": "Como corrigir ou melhorar"
    }
  ],
  "positives": ["Ponto positivo 1", "Ponto positivo 2"],
  "summary": "Resumo geral da review com parecer final"
}
"""


class ReviewerAgent(BaseAgent):
    def __init__(self):
        super().__init__("ReviewerAgent", REVIEWER_SYSTEM_PROMPT)

    def run(self, dev_output: dict, max_tokens: int = 4000, memory_context: str = "") -> dict:
        parts: list[str] = []
        if memory_context:
            parts.append(memory_context)
            parts.append("")
        parts.append(
            "Revise o seguinte código GoLang gerado por um agente de desenvolvimento:\n\n"
            + json.dumps(dev_output, indent=2, ensure_ascii=False)
        )
        prompt = "\n".join(parts)
        raw = super().run(prompt, max_tokens)
        return self.extract_json(raw, "ReviewerAgent")
