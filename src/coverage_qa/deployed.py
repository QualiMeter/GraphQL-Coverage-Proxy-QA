"""Shared policy for deployed FORMA; only reviewed read-only scenarios may run."""
import os
from uuid import UUID

import httpx
from fastapi import HTTPException

SAFE_DEPLOYED_SCENARIOS = frozenset({
    "tests/e2e/test_shop.py::test_storefront_catalog_and_cart",
})


def validate_deployed_scenarios(ids):
    if not ids or any(ident not in SAFE_DEPLOYED_SCENARIOS for ident in ids):
        raise HTTPException(400, "В рабочем режиме FORMA разрешены только сценарии из безопасного списка")


DEPLOYED_SERVICE_KEY = "forma-demo-shop"


def deployed_settings():
    url = os.environ.get("QA_DEPLOYED_COVERAGE_URL", "").strip().rstrip("/")
    shop = os.environ.get("QA_DEPLOYED_SHOP_API_URL", "").strip().rstrip("/")
    key = os.environ.get("COVERAGE_API_KEY", "").strip()
    return url, shop, key


def validate_deployed_run(run_id):
    try:
        parsed_id = str(UUID(run_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "Введите корректный UUID прогона из Coverage")
    if parsed_id != run_id.lower():
        raise HTTPException(400, "Введите UUID прогона без дополнительных символов")
    url, shop, key = deployed_settings()
    if not (url and shop and key):
        raise HTTPException(503, "Режим FORMA не настроен: добавьте адреса и ключ в защищённый файл VPS")
    try:
        with httpx.Client(timeout=8.0, trust_env=False) as client:
            services_response = client.get(url + "/api/services", headers={"X-Coverage-Api-Key": key})
            services_response.raise_for_status()
            service = next((row for row in services_response.json()
                            if row.get("key") == DEPLOYED_SERVICE_KEY), None)
            if not service:
                raise HTTPException(409, "Сервис FORMA Demo Shop отсутствует в Coverage")
            runs_response = client.get(url + "/api/runs",
                                       params={"serviceId": service["id"], "status": "running"},
                                       headers={"X-Coverage-Api-Key": key})
            runs_response.raise_for_status()
            if not any(row.get("id", "").lower() == parsed_id and row.get("status") == "running" for row in runs_response.json()):
                raise HTTPException(409, "Выбранный прогон FORMA не активен или принадлежит другому сервису")
            shop_response = client.post(shop + "/graphql",
                                        json={"query": "{ __typename }"})
            shop_response.raise_for_status()
            if shop_response.json().get("errors"):
                raise HTTPException(503, "Demo Shop API не отвечает корректно")
    except httpx.HTTPError:
        raise HTTPException(503, "Не удалось проверить Coverage или Demo Shop; проверьте адреса и доступ")
    except (ValueError, KeyError, TypeError):
        raise HTTPException(503, "Coverage вернул неожиданный ответ")
    return parsed_id, url, shop




def validate_deployed_target(run_id, coverage_url, upstream_url):
    result = validate_deployed_run(run_id)
    if (coverage_url or "").rstrip("/") != result[1] or (upstream_url or "").rstrip("/") != result[2]:
        raise HTTPException(400, "Deployed targets must match the server FORMA configuration")
    return result
