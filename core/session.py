class Session:
    def __init__(self, system_prompt):
        self.messages = [{"role": "system", "content": system_prompt}]

    def add(self, role, content):
        if role == "system":
            self.messages.insert(1, {"role": role, "content": content})
        else:
            self.messages.append({"role": role, "content": content})

    def seed(self, messages):
        for msg in reversed(messages):
            self.messages.insert(1, {"role": msg["role"], "content": msg["content"]})

    def tool_result(self, text):
        self.add("user", f"[tool result]\n{text}\n\nChoose your next action as JSON.")