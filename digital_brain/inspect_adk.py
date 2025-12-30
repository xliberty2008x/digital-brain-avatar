import inspect
from google.adk.agents.llm_agent import LlmAgent
from pydantic import BaseModel

print("Is LlmAgent a Pydantic model?", issubclass(LlmAgent, BaseModel))
print("LlmAgent fields:", LlmAgent.model_fields.keys())
