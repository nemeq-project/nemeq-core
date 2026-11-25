def test_openapi_watch_contract():
    from apiserver.openapi import build_openapi
    doc = build_openapi()
    path = doc["paths"]["/apis/nemeq.io/v1alpha1/zis"]["get"]
    names = [p["name"] for p in path["parameters"]]
    assert "fromRev" in names
    assert "from_rev" in names
    assert "watch" in names
    watch_schema = doc["components"]["schemas"]["WatchPage"]
    assert watch_schema["required"] == ["items", "metadata"]
    meta = watch_schema["properties"]["metadata"]
    assert meta["required"] == ["nextRev", "fromRev", "limit"]
    assert meta["properties"]["nextRev"]["type"] == "integer"
    item = watch_schema["properties"]["items"]["items"]
    assert set(item["required"]) == {"type", "rev", "object"}
    assert item["properties"]["type"]["enum"] == ["ADDED", "MODIFIED", "DELETED"]
    assert "List" in doc["components"]["schemas"]
