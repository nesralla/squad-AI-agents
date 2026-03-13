import logging

from sqlalchemy.orm import Session

from app.models.memory import AgentMemory, MemoryType
from app.services.embedding_service import generate_embedding, generate_query_embedding

logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Store
    # ------------------------------------------------------------------

    def store(
        self,
        task_id: int,
        memory_type: MemoryType,
        content: str,
        metadata: dict | None = None,
    ) -> AgentMemory:
        embedding = generate_embedding(content)
        memory = AgentMemory(
            task_id=task_id,
            memory_type=memory_type,
            content=content,
            embedding=embedding,
            metadata_=metadata,
        )
        self.db.add(memory)
        self.db.commit()
        self.db.refresh(memory)
        logger.info(
            f"Stored {memory_type.value} memory for task {task_id} "
            f"(id={memory.id}, has_embedding={embedding is not None})"
        )
        return memory

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_similar(
        self,
        query: str,
        memory_type: MemoryType | None = None,
        limit: int = 5,
    ) -> list[AgentMemory]:
        """Semantic search via pgvector cosine distance, with keyword fallback."""
        embedding = generate_query_embedding(query)

        if embedding is None:
            return self._keyword_search(query, memory_type, limit)

        q = self.db.query(AgentMemory).filter(AgentMemory.embedding.isnot(None))
        if memory_type:
            q = q.filter(AgentMemory.memory_type == memory_type)

        q = q.order_by(AgentMemory.embedding.cosine_distance(embedding)).limit(limit)
        return q.all()

    def _keyword_search(
        self,
        query: str,
        memory_type: MemoryType | None = None,
        limit: int = 5,
    ) -> list[AgentMemory]:
        """Simple ILIKE fallback when embeddings are unavailable."""
        q = self.db.query(AgentMemory)
        if memory_type:
            q = q.filter(AgentMemory.memory_type == memory_type)

        keywords = [w for w in query.lower().split() if len(w) > 3][:5]
        for word in keywords:
            q = q.filter(AgentMemory.content.ilike(f"%{word}%"))

        return q.order_by(AgentMemory.created_at.desc()).limit(limit).all()

    # ------------------------------------------------------------------
    # RAG context builder
    # ------------------------------------------------------------------

    def get_context_for_task(self, task_description: str) -> str:
        """Build a RAG context string from relevant past memories."""
        solutions = self.search_similar(
            task_description, MemoryType.TASK_SOLUTION, limit=3
        )
        patterns = self.search_similar(
            task_description, MemoryType.REVIEW_PATTERN, limit=2
        )
        fixes = self.search_similar(
            task_description, MemoryType.ERROR_FIX, limit=2
        )

        if not solutions and not patterns and not fixes:
            return ""

        parts: list[str] = []

        if solutions:
            parts.append("=== SOLUCOES ANTERIORES RELEVANTES ===")
            for mem in solutions:
                parts.append(f"[Task #{mem.task_id}] {mem.content}")
            parts.append("")

        if patterns:
            parts.append("=== PADROES DE REVIEW APRENDIDOS ===")
            for mem in patterns:
                parts.append(mem.content)
            parts.append("")

        if fixes:
            parts.append("=== CORRECOES ANTERIORES ===")
            for mem in fixes:
                parts.append(mem.content)
            parts.append("")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # High-level: store everything after a task completes
    # ------------------------------------------------------------------

    def store_task_completion(
        self,
        task_id: int,
        task_description: str,
        dev_output: dict,
        review_output: dict | None,
        build_success: bool,
        iterations: int,
    ) -> None:
        """Extract and persist memories from a completed task."""

        files = dev_output.get("files", [])
        file_list = ", ".join(f["path"] for f in files)
        summary = dev_output.get("summary", "")

        # 1) Task solution
        solution_content = (
            f"Tarefa: {task_description}\n"
            f"Solucao: {summary}\n"
            f"Arquivos: {file_list}\n"
            f"Build OK: {build_success}, Iteracoes: {iterations}"
        )
        self.store(
            task_id,
            MemoryType.TASK_SOLUTION,
            solution_content,
            {
                "files": [f["path"] for f in files],
                "build_success": build_success,
                "iterations": iterations,
            },
        )

        # 2) Review patterns
        if review_output:
            score = review_output.get("score", 0)
            issues = review_output.get("issues", [])
            positives = review_output.get("positives", [])
            review_summary = review_output.get("summary", "")

            if issues or positives:
                lines = [f"Review de: {task_description[:100]}"]
                lines.append(f"Score: {score}/10")

                if positives:
                    lines.append(
                        "Pontos positivos: " + "; ".join(positives[:3])
                    )

                critical_major = [
                    i
                    for i in issues
                    if i.get("severity") in ("critical", "major")
                ]
                if critical_major:
                    lines.append("Issues criticos/major:")
                    for issue in critical_major[:3]:
                        lines.append(
                            f"  - [{issue['severity']}] "
                            f"{issue.get('description', '')}"
                        )
                        if issue.get("suggestion"):
                            lines.append(f"    Fix: {issue['suggestion']}")

                if review_summary:
                    lines.append(f"Resumo: {review_summary[:200]}")

                self.store(
                    task_id,
                    MemoryType.REVIEW_PATTERN,
                    "\n".join(lines),
                    {
                        "score": score,
                        "issues_count": len(issues),
                        "approved": review_output.get("approved", False),
                    },
                )

        # 3) Error→fix patterns (when iterations > 1)
        if iterations > 1:
            fix_content = (
                f"Tarefa: {task_description[:100]}\n"
                f"Precisou de {iterations} iteracoes para corrigir.\n"
                f"Notas: {dev_output.get('notes', 'N/A')}"
            )
            self.store(task_id, MemoryType.ERROR_FIX, fix_content)
