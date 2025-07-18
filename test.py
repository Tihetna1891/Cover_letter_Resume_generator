from tasks import celery_app
print(celery_app.connection().connect())