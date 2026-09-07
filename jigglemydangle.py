from g4f.client import Client

query = "latest tech news"
client = Client()
response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": f"Search the web and summarize in less than 100 words: {query}"}]
)
print(response.choices[0].message.content)
