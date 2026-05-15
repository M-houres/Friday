"""配置管理 —— 从环境变量加载，Pydantic 校验"""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {
        "env_file": str(Path(__file__).parent.parent / "config" / ".env"),
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }

    # 环境
    environment: Literal["dev", "prod"] = "dev"

    # 数据库
    database_url: str = "postgresql+asyncpg://friday:friday_secret@localhost:5432/friday"
    redis_url: str = "redis://localhost:6379/0"

    # 模型
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    default_model: str = "deepseek-chat"
    default_fast_model: str = "deepseek-chat"

    # 沙盒
    sandbox_type: Literal["subprocess", "docker"] = "subprocess"

    # 日志
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "text"] = "json"

    # 限流 (RPM)
    rate_limit_deepseek_rpm: int = 500
    rate_limit_openai_rpm: int = 500
    rate_limit_anthropic_rpm: int = 4000

    # 熔断器
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_open_timeout_s: int = 30
    circuit_breaker_half_open_max: int = 3

    # 服务
    host: str = "0.0.0.0"
    port: int = 8000
    runtime_role: Literal["all", "api", "worker"] = "all"

    # Agent
    agent_pool_min_size: int = 2
    agent_pool_max_size: int = 10
    default_max_retries: int = 3
    default_tool_timeout_ms: int = 30000

    # Session
    session_message_limit_hot: int = 50
    session_message_limit_context: int = 20

    # 记忆
    memory_similarity_threshold: float = 0.85
    memory_embedding_model: str = "text-embedding-3-small"

    # 性能优化 (默认关闭, 稳定后开启)
    jit_enabled: bool = False
    jit_compile_threshold: int = 5
    speculative_enabled: bool = False
    interleaved_enabled: bool = False

    # 认证
    auth_mode: Literal["none", "api_key", "jwt"] = "none"
    api_keys: str = ""          # 逗号分隔
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"

    # 速率限制
    rate_limit_global_rpm: int = 6000
    rate_limit_user_rpm: int = 60
    rate_limit_ip_rpm: int = 30

    # CORS
    cors_allow_origins: str = "http://localhost:3000,http://localhost:5173,http://localhost:8080"

    # 异步任务
    async_jobs_enabled: bool = True
    async_jobs_default_priority: int = 5
    async_jobs_poll_interval_s: float = 1.0
    async_jobs_heartbeat_interval_s: float = 5.0
    async_jobs_stale_after_s: int = 120
    async_worker_name: str = "friday-worker"

    # 知识库
    knowledge_default_limit: int = 5

    # OpenTelemetry
    otel_exporter_otlp_endpoint: str = ""

    # S3 冷存储
    s3_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "friday-cold"
    s3_region: str = "us-east-1"
    s3_use_local_fs: bool = True
    s3_local_path: str = "data/cold"


settings = Settings()
