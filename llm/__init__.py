import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LLMConfig
from .client import BaseLLMClient, GeminiClient, OpenAIClient, create_client
from .prompts import (
    FORENSIC_QUERY_SYSTEM_PROMPT,
    FORENSIC_QUERY_USER_TEMPLATE,
    RESTORE_BATCH_USER_TEMPLATE,
    RESTORE_SYSTEM_PROMPT,
    RESTORE_USER_TEMPLATE,
)
from .query_engine import ForensicQueryEngine
from .restorer import TextRestorer
from .llm_pipeline import LLMReconstructor
from .metrics import (
    character_error_rate,
    exact_match_rate,
    average_cer,
    evaluate,
    snr_db,
    snr_delta_db,
)
