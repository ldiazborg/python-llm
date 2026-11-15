"""HTTP layer (FastAPI).

Modules:
    main.py     app factory, dependency providers, routes (/health, /ask)
    schemas.py  request/response models (validation + OpenAPI docs)
    errors.py   AppError -> HTTP status + uniform ErrorResponse body
"""
