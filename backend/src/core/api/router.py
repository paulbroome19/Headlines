from fastapi import APIRouter

from core.api.routes.health import router as health_router
from core.api.routes.data import router as data_router
from core.api.routes.profiles import router as profiles_router
from core.api.routes.feeds import router as feeds_router
from core.api.routes.scripts import router as scripts_router
from core.api.routes.audio import router as audio_router
from core.api.routes.dev import router as dev_router


router = APIRouter()

router.include_router(health_router)
router.include_router(data_router)
router.include_router(profiles_router)
router.include_router(feeds_router)
router.include_router(scripts_router)
router.include_router(audio_router)
router.include_router(dev_router)