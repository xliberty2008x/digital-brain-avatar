try:
    from google.adk.plugins.reflect_retry_tool_plugin import ReflectRetryToolPlugin
    print("DOCSTRING:", ReflectRetryToolPlugin.__doc__)
    print("Methods:", dir(ReflectRetryToolPlugin))
except Exception as e:
    print(e)
