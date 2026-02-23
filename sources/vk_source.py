import vk_api

def get_vk_info(token: str, user_id: str) -> dict:
    try:
        vk_session = vk_api.VkApi(token=token)
        vk = vk_session.get_api()

        # Определяем числовой id или screen_name
        try:
            uid = int(user_id)
            users = vk.users.get(user_ids=uid, fields="bdate,city,country,education,followers_count,occupation,relation,status,about,activities,interests,music,movies,tv,books,games")
        except ValueError:
            users = vk.users.get(user_ids=user_id, fields="bdate,city,country,education,followers_count,occupation,relation,status,about,activities,interests,music,movies,tv,books,games")

        if not users:
            return {"error": "Пользователь не найден"}

        user = users[0]
        uid = user["id"]

        # Получаем посты
        posts = []
        try:
            wall = vk.wall.get(owner_id=uid, count=20, filter="owner")
            for p in wall.get("items", []):
                if p.get("text"):
                    posts.append(p["text"])
        except:
            pass

        # Получаем группы
        groups = []
        try:
            grps = vk.groups.get(user_id=uid, extended=1, count=20)
            for g in grps.get("items", []):
                groups.append(g.get("name", ""))
        except:
            pass

        return {
            "source": "vk",
            "id": uid,
            "name": f"{user.get('first_name','')} {user.get('last_name','')}",
            "status": user.get("status", ""),
            "about": user.get("about", ""),
            "city": user.get("city", {}).get("title", "") if user.get("city") else "",
            "country": user.get("country", {}).get("title", "") if user.get("country") else "",
            "bdate": user.get("bdate", ""),
            "followers": user.get("followers_count", 0),
            "interests": user.get("interests", ""),
            "music": user.get("music", ""),
            "movies": user.get("movies", ""),
            "books": user.get("books", ""),
            "activities": user.get("activities", ""),
            "occupation": user.get("occupation", {}).get("name", "") if user.get("occupation") else "",
            "posts": posts,
            "groups": groups,
            "profile_url": f"https://vk.com/id{uid}"
        }
    except Exception as e:
        return {"error": str(e)}
