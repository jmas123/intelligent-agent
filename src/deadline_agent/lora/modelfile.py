"""Generate Ollama Modelfile for LoRA-merged GGUF models."""

from deadline_agent.extraction.extractor import EXTRACTION_SYSTEM_PROMPT
from deadline_agent.reasoning.engine import DIGEST_SYSTEM_PROMPT


def generate_modelfile(
    gguf_path: str,
    task: str = "extraction",
    temperature: float = 0.1,
    num_ctx: int = 4096,
    top_p: float = 0.9,
) -> str:
    """Generate an Ollama Modelfile for a LoRA-merged GGUF.

    Args:
        gguf_path: Path to the merged GGUF file.
        task: "extraction" or "reasoning" — determines the system prompt.
        temperature: Sampling temperature (low for structured output).
        num_ctx: Context window size.
        top_p: Top-p sampling parameter.

    Returns:
        Modelfile content as a string.
    """
    system_prompt = EXTRACTION_SYSTEM_PROMPT if task == "extraction" else DIGEST_SYSTEM_PROMPT

    return f"""FROM {gguf_path}

PARAMETER temperature {temperature}
PARAMETER top_p {top_p}
PARAMETER num_ctx {num_ctx}

SYSTEM \"\"\"{system_prompt}\"\"\"
"""
