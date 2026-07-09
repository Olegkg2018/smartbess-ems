from fastapi import APIRouter, HTTPException, Depends
from src.core.redis import get_job_status
from src.core.security import RoleChecker

router = APIRouter()

@router.get("/{job_id}", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_job_status_endpoint(job_id: str):
    job = get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    response = {
        "job_id": job["job_id"],
        "status": job["status"],
        "created_at": job["created_at"]
    }
    
    if job["status"] == "completed":
        response["progress_pct"] = 100
        # Link to actual result or the result itself
        response["result"] = job.get("result")
    elif job["status"] == "failed":
        response["progress_pct"] = 0
        response["error"] = job.get("error")
    else: # running / pending
        response["progress_pct"] = 45 if job["status"] == "running" else 0
        
    return response
