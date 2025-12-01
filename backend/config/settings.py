from pydantic_settings import BaseSettings
from typing import Optional
import os

class Settings(BaseSettings):
    # Application
    app_name: str = "Matrix CRM Interceptor"
    debug: bool = False
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    
    # Matrix
    matrix_homeserver_url: str
    matrix_access_token: str
    matrix_user_id: str
    matrix_device_id: str = "CRMBOT"
    
    # Matrix E2EE Recovery Key (NEW FIELD)
    matrix_recovery_key: Optional[str] = None
    
    # App Service
    app_service_id: str = "adaire_crm"
    app_service_token: str = "your_app_service_token"
    app_service_url: str = "http://localhost:8000/_matrix/app/v1"
    
    # Odoo
    odoo_url: Optional[str] = None
    odoo_database: Optional[str] = None
    odoo_username: Optional[str] = None
    odoo_password: Optional[str] = None
    odoo_api_key: Optional[str] = None
    
    # Redis
    redis_url: str = "redis://localhost:6379"
    redis_password: Optional[str] = None
    
    # Security
    secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    
    # Webhook Secrets
    matrix_webhook_secret: Optional[str] = None
    crm_webhook_secret: Optional[str] = None
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        # Allow extra fields (like message_recovery_key if you have it in .env)
        extra = "ignore"

settings = Settings()