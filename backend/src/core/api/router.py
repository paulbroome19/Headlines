from fastapi import APIRouter

from core.api.routes.health import router as health_router
from core.api.routes.data import router as data_router

router = APIRouter()

router.include_router(health_router)
router.include_router(data_router)