import json
import logging

from app.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

SECURITY_SYSTEM_PROMPT = """Voce e um especialista em seguranca de aplicacoes (AppSec) focado em GoLang.
Sua funcao e realizar uma auditoria de seguranca rigorosa no codigo gerado.

Analise obrigatoria (OWASP Top 10 + Go-specific):
1. SQL Injection — queries nao parametrizadas, concatenacao de strings em SQL
2. Command Injection — uso de exec.Command com input nao sanitizado
3. Path Traversal — manipulacao de caminhos de arquivo sem validacao
4. XSS — output nao sanitizado em respostas HTML/JSON
5. Exposicao de dados sensiveis — passwords em logs, tokens em respostas, secrets hardcoded
6. Autenticacao/Autorizacao — endpoints sem protecao, JWT mal configurado
7. Race conditions — acesso concorrente a dados compartilhados sem mutex/channel
8. Denial of Service — falta de rate limiting, timeouts, limites de tamanho
9. Dependencias inseguras — versoes conhecidamente vulneraveis
10. Criptografia — uso de crypto/md5, crypto/sha1 para hashing de senhas
11. Error disclosure — stack traces ou mensagens internas expostas ao cliente
12. SSRF — requisicoes HTTP com URL controlada pelo usuario

Niveis de severidade:
- critical: Vulnerabilidade exploravel remotamente, impacto alto (RCE, SQLi, auth bypass)
- high: Vulnerabilidade com impacto significativo (data leak, privilege escalation)
- medium: Risco moderado que requer condicoes especificas (race condition, DoS)
- low: Boas praticas nao seguidas, risco teorico (logging verboso, error disclosure)
- info: Sugestao de melhoria sem risco direto

Formato de resposta OBRIGATORIO — retorne APENAS um JSON valido, sem texto antes ou depois:
{
  "secure": true,
  "risk_score": 3,
  "vulnerabilities": [
    {
      "severity": "critical|high|medium|low|info",
      "category": "SQL Injection",
      "file": "internal/repository/user_repo.go",
      "line_hint": "linha ~45",
      "description": "Query SQL construida com concatenacao de string",
      "impact": "Atacante pode ler/modificar dados do banco",
      "remediation": "Usar query parametrizada com $1, $2",
      "cwe": "CWE-89"
    }
  ],
  "positive_practices": [
    "Uso correto de parametros em queries SQL",
    "Tratamento adequado de erros sem expor internals"
  ],
  "summary": "Resumo geral da postura de seguranca do codigo"
}
"""


class SecurityAgent(BaseAgent):
    def __init__(self):
        super().__init__("SecurityAgent", SECURITY_SYSTEM_PROMPT)

    def scan(self, dev_output: dict, max_tokens: int = 4000) -> dict:
        """Perform a security audit on the generated code."""
        prompt = (
            "Realize uma auditoria de seguranca completa no seguinte codigo GoLang:\n\n"
            + json.dumps(dev_output, indent=2, ensure_ascii=False)
        )
        raw = super().run(prompt, max_tokens)
        return self.extract_json(raw, "SecurityAgent")

    def has_critical_issues(self, scan_result: dict) -> bool:
        """Check if there are critical or high severity vulnerabilities."""
        vulns = scan_result.get("vulnerabilities", [])
        return any(v.get("severity") in ("critical", "high") for v in vulns)

    def get_remediation_feedback(self, scan_result: dict) -> str | None:
        """Build feedback string from critical/high vulnerabilities for DevAgent to fix."""
        vulns = scan_result.get("vulnerabilities", [])
        critical_high = [v for v in vulns if v.get("severity") in ("critical", "high")]

        if not critical_high:
            return None

        lines = ["Security vulnerabilities that MUST be fixed:"]
        for v in critical_high:
            sev = v.get("severity", "?").upper()
            cat = v.get("category", "")
            desc = v.get("description", "")
            fix = v.get("remediation", "")
            fname = v.get("file", "")
            lines.append(f"  [{sev}] {cat} in {fname}: {desc}")
            if fix:
                lines.append(f"         Fix: {fix}")

        return "\n".join(lines)
