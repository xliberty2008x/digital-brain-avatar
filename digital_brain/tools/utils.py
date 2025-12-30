import asyncio

# Retry Generator
async def retry_generator(async_gen_func, max_retries=3, initial_delay=1):
    """
    Wraps an async generator function with retry logic for ConnectionError and TimeoutError.
    
    Args:
        async_gen_func: A callable that returns an async generator.
        max_retries: Maximum number of retries.
        initial_delay: Initial delay in seconds (exponential backoff).
    """
    delay = initial_delay
    for attempt in range(max_retries + 1):
        try:
            # Create a fresh generator on each attempt
            gen = async_gen_func()
            async for item in gen:
                yield item
            return  # Success, exit cleanly
            
        except (ConnectionError, asyncio.TimeoutError) as e:
            if attempt < max_retries:
                print(f"⚠️ Network error: {e}. Retrying in {delay}s... (Attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                print(f"❌ Failed after {max_retries} retries.")
                raise e  # Propagate the error after final failure


def exit_loop_tool(context) -> str:
    """
    Exits the critique loop.
    Call this tool when the Cypher queries are valid and ready for execution.
    """
    context.session.state["loop_decision"] = "EXIT" 
    return "Loop exit requested."