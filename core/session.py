class Session:
    def __init__(self, system_prompt):
        self.messages = [{"role": "system", "content": system_prompt}]

    def add(self, role, content):
        self.messages.append({"role": role, "content": content})

    def tool_result(self, text):
        self.add("user", f"[tool result]\n{text}\n\nChoose your next action as JSON.")