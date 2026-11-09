import os
from fastapi import FastAPI, File, UploadFile, Form, Header, HTTPException
import uvicorn
from typing import List

app = FastAPI()
UPLOAD_DIR = "test_captures"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/recognize")
async def receive_cam_images(
    device_id: str = Form(...),
    seq: int = Form(...),
    direction: str = Form(...),
    images: List[UploadFile] = File(...),
    x_api_key: str = Header(None)
):
    print(f"\n[MOCK SERVER] >>> Nhận request từ: {device_id} | Seq={seq} | Hướng={direction}")
    print(f"[MOCK SERVER] X-API-Key: {x_api_key}")
    print(f"[MOCK SERVER] Số ảnh nhận được: {len(images)}")
    
    saved_files = []
    for idx, img in enumerate(images):
        file_path = os.path.join(UPLOAD_DIR, f"seq_{seq}_{direction}_frame_{idx}.jpg")
        content = await img.read()
        with open(file_path, "wb") as f:
            f.write(content)
        print(f" -> Đã lưu ảnh: {file_path} ({len(content)} bytes)")
        saved_files.append(file_path)

    return {"status": "SUCCESS", "seq": seq, "saved": saved_files}

if __name__ == "__main__":
    print("[MOCK SERVER] Đang lắng nghe tại cổng 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)