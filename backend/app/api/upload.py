from pathlib import Path
import traceback

import pandas as pd
from fastapi import APIRouter, UploadFile, File, HTTPException

# Qwen runs in the AI Model Service; this calls it over HTTP.
from app.services import ai_client

router = APIRouter(prefix="/upload", tags=["Upload"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPLOAD_DIR = PROJECT_ROOT / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/")
async def upload_file(file: UploadFile = File(...)):

    try:

        if not file.filename:
            raise HTTPException(400, "No file selected.")

        if not file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
            raise HTTPException(
                400,
                "Only CSV and Excel files are supported."
            )

        file_path = UPLOAD_DIR / file.filename

        with open(file_path, "wb") as f:
            f.write(await file.read())

        if file.filename.lower().endswith(".csv"):
            df = pd.read_csv(file_path)

        else:
            df = pd.read_excel(file_path)

        if df.empty:
            raise HTTPException(400, "Uploaded file is empty.")

        feedback_column = None

        possible_columns = [
            "feedback",
            "review",
            "comment",
            "customer feedback",
            "remarks"
        ]

        for col in df.columns:
            if col.strip().lower() in possible_columns:
                feedback_column = col
                break

        if feedback_column is None:
            for col in df.columns:
                if "feedback" in col.lower():
                    feedback_column = col
                    break

        if feedback_column is None:
            raise HTTPException(
                400,
                f"No feedback column found. Available columns: {list(df.columns)}"
            )

        feedback_list = (
            df[feedback_column]
            .fillna("")
            .astype(str)
            .tolist()
        )

        try:
            ai_analysis = await ai_client.analyze_feedback(feedback_list)
        except ai_client.AIServiceError as ai_error:
            raise HTTPException(ai_error.status_code, ai_error.detail)

        return {
            "success": True,
            "filename": file.filename,
            "rows": len(df),
            "columns": list(df.columns),
            "feedback_column": feedback_column,
            "preview": df.head(5).fillna("").to_dict(orient="records"),
            "analysis": ai_analysis
        }

    except HTTPException:
        raise

    except Exception as e:

        print("\n===== UPLOAD ERROR =====")
        traceback.print_exc()
        print("========================\n")

        raise HTTPException(500, str(e))