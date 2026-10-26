def classify_domain(domain: str, whitelist: dict[str, list[str]]) -> str:
    normalized_domain = domain.lower().removeprefix("www.").rstrip(".")

    for tier in ("tier_a", "tier_b", "tier_c"):
        for allowed_domain in whitelist.get(tier, []):
            allowed = (
                allowed_domain.lower().removeprefix("www.").lstrip(".").rstrip(".")
            )
            if normalized_domain == allowed or normalized_domain.endswith(
                f".{allowed}"
            ):
                return tier

    return "unlisted"


def source_independence_domain(domain: str, whitelist: dict[str, list[str]]) -> str:
    normalized_domain = domain.lower().removeprefix("www.").rstrip(".")

    for tier in ("tier_a", "tier_b", "tier_c"):
        for allowed_domain in whitelist.get(tier, []):
            allowed = (
                allowed_domain.lower().removeprefix("www.").lstrip(".").rstrip(".")
            )
            if allowed_domain.startswith(".") and normalized_domain.endswith(
                f".{allowed}"
            ):
                prefix = normalized_domain[: -(len(allowed) + 1)]
                return f"{prefix.rsplit('.', 1)[-1]}.{allowed}"
            if normalized_domain == allowed or normalized_domain.endswith(
                f".{allowed}"
            ):
                return allowed

    return normalized_domain
