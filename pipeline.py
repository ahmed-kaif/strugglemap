import vertexai
import os 
import subprocess
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from langchain_google_vertexai import ChatVertexAI
from langchain_core.prompts import ChatPromptTemplate

# 1. Initialize Vertex AI
# Replace with your actual project ID and a supported region
vertexai.init(project="project-88f5c560-5b00-4f90-a21", location="us-central1") 

app = FastAPI()
# Mount the media directory so the frontend can access the videos
os.makedirs("media", exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")

class ConceptBreakdown(BaseModel):
    sub_topics: list[str] = Field(description="A list of 3 sequential sub-topics to explain the concept.")
    core_friction_point: str = Field(description="The exact logical leap where students usually get stuck.")

flash_model = ChatVertexAI(model_name="gemini-2.5-flash", temperature=0.2)
pro_model = ChatVertexAI(model_name="gemini-2.5-pro", temperature=0.1)

breakdown_agent = flash_model.with_structured_output(ConceptBreakdown)

@app.post("/generate-lesson")
async def generate_lesson(topic: str):
    # --- AGENT 1: The Breakdown ---
    breakdown_prompt = ChatPromptTemplate.from_template(
        "You are an expert math teacher. Break down the mathematical concept '{topic}' "
        "into 3 simple, visual steps suitable for a 3Blue1Brown style animation."
    )
    chain = breakdown_prompt | breakdown_agent
    breakdown_result = chain.invoke({"topic": topic})
    
# --- AGENT 2: The Manim Coder ---
    coder_prompt = ChatPromptTemplate.from_template(
        "You are an expert Python developer specializing in the Manim Community library. "
        "Write executable Manim code to animate the following mathematical steps: {steps}. "
        "Focus on the friction point: {friction}. "
        "IMPORTANT RULES: "
        "1. Output ONLY pure Python code. No markdown formatting, no explanations. "
        "2. The main class MUST be named exactly 'MathScene'."
        "3. Keep animations simple (Create, Write, FadeIn) and stick to 2D math objects. "
        "4. STRICT POSITIONING RULE: To position objects, you MUST ONLY use standard Manim "
        "methods: .move_to(), .next_to(), .shift(), .to_edge(), or .to_corner(). "
        "NEVER invent or use undocumented methods like .at_self_point() or .set_position(). "
        "5. STRICT COORDINATE RULE: Manim requires 3D vectors for everything. If you use lists "
        "or arrays for coordinates, they MUST have 3 elements (e.g., [x, y, 0]). NEVER use 2D "
        "coordinates like [x, y]. Prefer built-in constants like ORIGIN, UP, DOWN, LEFT, RIGHT."
    )

    coder_chain = coder_prompt | pro_model
    manim_code_response = coder_chain.invoke({
        "steps": breakdown_result.sub_topics,
        "friction": breakdown_result.core_friction_point
    })
    
    # Clean the output
    raw_code = manim_code_response.content.replace("```python", "").replace("```", "").strip()
    
    # --- THE EXECUTION SANDBOX ---
    scene_filename = "temp_scene.py"
    
    # 1. Write the code to a file
    with open(scene_filename, "w") as f:
        f.write("from manim import *\n\n") # Ensure manim is imported
        f.write(raw_code)
        
    # 2. Run Manim via subprocess
    # -ql means Quality Low (480p, 15fps) -> Crucial for fast hackathon generation
    # --media_dir sets where the output goes
    cmd = ["manim", "-ql", scene_filename, "MathScene", "--media_dir", "media"]
    
    try:
        # Run the command and wait for it to finish
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        # If the LLM wrote bad code and Manim crashes, log the error
        print(f"Manim Error Log:\n{e.stderr}")
        raise HTTPException(status_code=500, detail="The AI generated invalid Manim code. Try again.")

    # 3. Construct the video URL
    # Manim's default output path structure for -ql is: media/videos/<filename>/480p15/<classname>.mp4
    video_url = "/media/videos/temp_scene/480p15/MathScene.mp4"
    
    return {
        "status": "Success",
        "breakdown": breakdown_result,
        "video_url": f"http://localhost:8000{video_url}",
        "raw_code": raw_code # Keeping this for debugging
    }
