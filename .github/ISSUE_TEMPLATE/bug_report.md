---
name: Bug report
about: Something behaves differently than documented
labels: bug
---

**What happened**

<!-- Include the traceback if there is one. -->

**What you expected**

**Reproduction**

```python
import asyncio
import aiopyinfrahub


async def main():
    async with aiopyinfrahub.api("https://infrahub.example.com", token="...") as ih:
        ...


asyncio.run(main())
```

**Versions**

- aiopyinfrahub:
- Infrahub:
- Python:

**Anything else**

<!-- If the problem involves a specific kind, the relevant part of its schema
(attributes, relationships, default_filter, human_friendly_id) is very useful.
Does infrahub-sdk behave differently here? That is worth knowing too. -->
