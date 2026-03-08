"""
views.py
--------
Page views
  GET /            → templates/index.html
  GET /product/    → templates/product.html
  GET /about/      → templates/aboutus.html

API endpoints
  POST  /api/images  → save uploaded images, return session_id
  PATCH /api/price   → run vision + web-search, return eco alternatives
"""

import json
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .services.vision import identify_objects
from .services.reasoning import find_eco_alternatives


# ── Page views ────────────────────────────────────────────────────────────────

def page_home(request):
    return render(request, "index.html")

def page_product(request):
    return render(request, "product.html")

def page_about(request):
    return render(request, "aboutus.html")


# ── Helpers ───────────────────────────────────────────────────────────────────

SESSIONS_ROOT = Path(settings.MEDIA_ROOT) / "sessions"
SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _session_dir(session_id: str) -> Path:
    return SESSIONS_ROOT / session_id


def _image_url(request, session_id: str, filename: str) -> str:
    return request.build_absolute_uri(f"/media/sessions/{session_id}/{filename}")


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _build_image_list(request, session_id: str, image_paths: list) -> list:
    return [
        {
            "url":   _image_url(request, session_id, p.name),
            "label": p.stem.replace("_", " ").title(),
        }
        for p in image_paths
    ]


def _cleanup_session(session_dir: Path) -> None:
    try:
        shutil.rmtree(session_dir, ignore_errors=True)
    except Exception:
        pass


# ── POST /api/images ──────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def upload_images(request):
    if request.method == "OPTIONS":
        return JsonResponse({}, status=200)

    if not request.FILES:
        return _error("No images provided.")

    max_images = settings.MAX_IMAGES_PER_REQUEST
    uploaded_files = [f for key in request.FILES for f in request.FILES.getlist(key)]

    if len(uploaded_files) > max_images:
        return _error(f"Maximum {max_images} images per request.")

    for f in uploaded_files:
        ext = Path(f.name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            return _error(f"Unsupported file type: {f.name}")
        if f.size > 20 * 1024 * 1024:
            return _error(f"File too large: {f.name}. Max 20 MB.")

    session_id = str(uuid.uuid4())
    session_dir = _session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for i, f in enumerate(uploaded_files):
        ext = Path(f.name).suffix.lower() or ".jpg"
        filename = f"image_{i:02d}{ext}"
        dest = session_dir / filename
        with open(dest, "wb") as out:
            for chunk in f.chunks():
                out.write(chunk)
        saved.append(filename)

    return JsonResponse(
        {"session_id": session_id, "image_count": len(saved), "images": saved},
        status=201,
    )


# ── PATCH /api/price ──────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["PATCH", "OPTIONS"])
def analyse_and_price(request):
    if request.method == "OPTIONS":
        return JsonResponse({}, status=200)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _error("Request body must be valid JSON.")

    session_id = body.get("session_id", "").strip()
    if not session_id:
        return _error("session_id is required.")

    raw_price = body.get("max_price")
    max_price = None
    if raw_price is not None:
        try:
            max_price = float(raw_price)
            if max_price <= 0:
                return _error("max_price must be a positive number.")
        except (TypeError, ValueError):
            return _error("max_price must be a number.")

    session_dir = _session_dir(session_id)
    if not session_dir.exists():
        return _error("Session not found or already expired.", status=404)

    image_paths = sorted(
        p for p in session_dir.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS
    )
    if not image_paths:
        return _error("No images found for this session.", status=404)

    # Step 1: Vision
    try:
        objects = identify_objects(image_paths)
    except Exception as exc:
        return _error(f"Vision analysis failed: {exc}", status=502)

    if not objects:
        return JsonResponse({
            "images":           _build_image_list(request, session_id, image_paths),
            "detected_objects": [],
            "products":         [],
            "message":          "No recognisable products found in the uploaded images.",
        })

    # Step 2: Eco search
    try:
        products = find_eco_alternatives(objects, max_price=max_price)
    except Exception as exc:
        return _error(f"Eco search failed: {exc}", status=502)

    images_out = _build_image_list(request, session_id, image_paths)
    _cleanup_session(session_dir)

    return JsonResponse({
        "images":           images_out,
        "detected_objects": [o.get("name") for o in objects],
        "products":         products,
    })
