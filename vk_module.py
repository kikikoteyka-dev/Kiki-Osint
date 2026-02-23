import vk_api
from config import VK_TOKEN

def get_vk_profile(user_id_or_url: str) -> dict:
    """Получает профиль пользователя VK по username, URL или ID"""
    try:
        session = vk_api.VkApi(token=VK_TOKEN)
        vk = session.get_api()

        # Нормализация запроса
        query = user_id_or_url.strip()
        if "vk.com/" in query:
            query = query.rstrip("/").split("/")[-1]
            if "?" in query:
                query = query.split("?")[0]

        # Определяем user_id
        if query.lstrip("-").isdigit():
            user_id = int(query)
        else:
            resolved = vk.utils.resolveScreenName(screen_name=query)
            if not resolved or resolved.get("type") not in ("user", None):
                return {"error": f"User '{query}' not found"}
            user_id = resolved["object_id"]

        # Основная инфа (расширенные поля)
        users = vk.users.get(
            user_ids=user_id,
            fields="photo_max_orig,city,country,bdate,sex,followers_count,"
                   "relation,status,site,occupation,universities,schools,"
                   "personal,last_seen,is_closed"
        )
        if not users:
            return {"error": "User not found"}
        p = users[0]

        if p.get("is_closed") and not p.get("can_access_closed"):
            # Закрытый профиль — возвращаем что есть
            return {
                "source": "VK",
                "name": f"{p.get('first_name','')} {p.get('last_name','')}",
                "id": p.get("id"),
                "closed": True,
                "photo": p.get("photo_max_orig",""),
                "city": p.get("city",{}).get("title","") if p.get("city") else "",
                "country": p.get("country",{}).get("title","") if p.get("country") else "",
            }

        # Последние посты (до 10)
        posts = []
        try:
            wall = vk.wall.get(owner_id=user_id, count=10, filter="owner")
            for item in wall.get("items", []):
                text = item.get("text","").strip()
                if text:
                    posts.append(text[:400])
        except:
            pass

        # Группы
        groups = []
        try:
            grps = vk.groups.get(user_id=user_id, count=30, extended=1)
            for g in grps.get("items", []):
                if isinstance(g, dict):
                    groups.append(g.get("name",""))
        except:
            pass

        # Образование
        edu = []
        for uni in p.get("universities", []):
            edu.append(uni.get("name",""))
        for school in p.get("schools", []):
            edu.append(school.get("name",""))

        # Последний онлайн
        last_seen = ""
        if p.get("last_seen"):
            import datetime
            ts = p["last_seen"].get("time", 0)
            if ts:
                dt = datetime.datetime.fromtimestamp(ts)
                last_seen = dt.strftime("%d.%m.%Y %H:%M")

        sex_map = {1: "female", 2: "male"}

        return {
            "source": "VK",
            "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "id": p.get("id"),
            "status": p.get("status",""),
            "city": p.get("city",{}).get("title","") if p.get("city") else "",
            "country": p.get("country",{}).get("title","") if p.get("country") else "",
            "bdate": p.get("bdate",""),
            "sex": sex_map.get(p.get("sex",0),""),
            "followers": p.get("followers_count",0),
            "relation": _relation(p.get("relation",0)),
            "site": p.get("site",""),
            "occupation": p.get("occupation",{}).get("name","") if p.get("occupation") else "",
            "education": edu[:5],
            "last_seen": last_seen,
            "photo": p.get("photo_max_orig",""),
            "posts": posts,
            "groups": groups[:20],
            "closed": False,
        }
    except vk_api.exceptions.ApiError as e:
        return {"error": f"VK API: {e}"}
    except Exception as e:
        return {"error": str(e)}

def _relation(r):
    m = {1:"single",2:"in a relationship",3:"engaged",
         4:"married",5:"it's complicated",6:"actively looking",
         7:"in love",8:"in civil union"}
    return m.get(r,"")
