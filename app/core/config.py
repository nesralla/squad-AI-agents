import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/ai_squad")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_ALLOWED_CHAT_IDS: list[int] = [
        int(x) for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if x.strip()
    ]

    # Git config
    GIT_REPO_URL: str = os.getenv("GIT_REPO_URL", "")
    GIT_REPO_PATH: str = os.getenv("GIT_REPO_PATH", "/tmp/ia-squad-repo")
    GIT_USERNAME: str = os.getenv("GIT_USERNAME", "IA Dev Squad")
    GIT_EMAIL: str = os.getenv("GIT_EMAIL", "ia-squad@devbot.local")
    GIT_TOKEN: str = os.getenv("GIT_TOKEN", "")
    GIT_MAIN_BRANCH: str = os.getenv("GIT_MAIN_BRANCH", "main")

    # API internal URL used by the telegram bot
    API_BASE_URL: str = os.getenv("API_BASE_URL", "http://api:8000")

    # Memory / Embeddings (Phase 3)
    VOYAGE_API_KEY: str = os.getenv("VOYAGE_API_KEY", "")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "voyage-code-3")
    MEMORY_ENABLED: bool = os.getenv("MEMORY_ENABLED", "true").lower() in ("true", "1", "yes")

    # Jira Cloud integration
    JIRA_ENABLED: bool = os.getenv("JIRA_ENABLED", "false").lower() in ("true", "1", "yes")
    JIRA_BASE_URL: str = os.getenv("JIRA_BASE_URL", "")          # e.g. https://your-domain.atlassian.net
    JIRA_USER_EMAIL: str = os.getenv("JIRA_USER_EMAIL", "")      # Atlassian account email
    JIRA_API_TOKEN: str = os.getenv("JIRA_API_TOKEN", "")        # API token from id.atlassian.com
    JIRA_PROJECT_KEY: str = os.getenv("JIRA_PROJECT_KEY", "")    # e.g. "DEV", "SQUAD"
    JIRA_POLL_INTERVAL: int = int(os.getenv("JIRA_POLL_INTERVAL", "60"))  # seconds
    JIRA_STATUS_TODO: str = os.getenv("JIRA_STATUS_TODO", "To Do")
    JIRA_STATUS_IN_PROGRESS: str = os.getenv("JIRA_STATUS_IN_PROGRESS", "In Progress")
    JIRA_STATUS_DONE: str = os.getenv("JIRA_STATUS_DONE", "Done")
    JIRA_LABEL_TRIGGER: str = os.getenv("JIRA_LABEL_TRIGGER", "ai-squad")  # label that triggers processing


settings = Settings()
