from dotenv import load_dotenv
from pathlib import Path

# Load .env from the project root (same dir as this file), override=True ensures
# fresh values are always picked up even if env vars were set previously.
_env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI , HTTPException 
from pydantic import BaseModel
from typing import Any
from agents.easyecom_agent import EasyEcomAgent
from config import MAIN_API_PORT
import json
import uuid

app = FastAPI(title="EasyEcom AI Assistant API")


app.add_middleware(
    CORSMiddleware, 
    allow_origins = ["*"],
    allow_credentials = True,
    allow_methods = ["*"] ,
    allow_headers = ["*"], 
)

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.session_id:
            self.session_id = str(uuid.uuid4())

class ChatResponse(BaseModel):
    response: str

agent = EasyEcomAgent()

@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Streaming chat endpoint"""
    async def generate():
        try:
            async for event in agent.process_message_streaming(request.message, request.session_id):
                event_type = event.get("type", "token")
                if event_type == "token":
                    yield f"data: {json.dumps({'token': event.get('data', '')})}\n\n"
                elif event_type == "tool_use":
                    yield f"data: {json.dumps({'tool_use': event.get('data', {})})}\n\n"
                elif event_type == "result":
                    yield f"data: {json.dumps({'result': event.get('data', '')})}\n\n"
                elif event_type == "error":
                    yield f"data: {json.dumps({'error': event.get('data', '')})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/plain")

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Standard chat endpoint"""
    try:
        result = await agent.process_message(request.message, request.session_id)
        return ChatResponse(response=result["response"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=MAIN_API_PORT)

