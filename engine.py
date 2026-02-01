async def get_response(self, prompt, system="You are a helpful assistant", force_light=False):
    client = random.choice(self.clients)
    
    # Жестко обрезаем промпт до ~4500 символов, чтобы гарантированно влезть в 6000 TPM
    safe_prompt = prompt[:4500] 
    
    # На таких низких лимитах 8b - единственный вариант
    selected_model = "llama-3.1-8b-instant"
    
    try:
        res = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": safe_prompt}
            ],
            model=selected_model
        )
        return res.choices[0].message.content
    except Exception as e:
        logger.error(f"Groq API Error: {e}")
        return "⚠️ Сервис перегружен. Попробуйте задать вопрос через минуту."