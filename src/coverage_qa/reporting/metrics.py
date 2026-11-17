def graph_field_hits(graph: dict) -> dict[str, int]:
    hits = {}
    for schema_type in graph["types"]:
        if schema_type.get("kind", "OBJECT") != "OBJECT":
            continue
        for field in schema_type["fields"]:
            count = field["hits"]
            coordinate = f"{schema_type['name']}.{field['name']}"
            if type(count) is not int or count < 0:
                raise ValueError("Invalid field hit count")
            if coordinate in hits:
                raise ValueError("Duplicate field coordinate")
            hits[coordinate] = count
    return hits
