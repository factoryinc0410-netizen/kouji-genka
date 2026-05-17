"""マスタ管理ルーター。

現状は qualifications (q_qualifications) のみ管理する。将来的に
cc_workers などの他マスタを追加する際もこの prefix (/master/...) に集約する。

権限 (RBAC): すべてのエンドポイントで ``RequirePermission("qualifications_master",
"manager")`` を要求する (閲覧も編集も manager 限定)。``is_admin=True`` の
ユーザーは ``has_permission`` のショートカットで素通し。
q_certificates から FK 参照されているため、物理削除は許容しない
(is_active のトグルで論理無効化する)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from web_app.core.database import get_db
from web_app.core.dependencies import RequirePermission
from web_app.core.templates import templates as _templates

logger = logging.getLogger("web_app.master")

router = APIRouter(prefix="/master", tags=["master"])

# RBAC: マスタは閲覧も編集も manager 限定 (general に開放しない)。
# is_admin=True のユーザーは has_permission のショートカットで素通し。
_RequireMasterManager = RequirePermission("qualifications_master", "manager")


# ────────────────────────────────────────────
# データ取得ヘルパ
# ────────────────────────────────────────────

async def _fetch_qualifications(
    db,
    *,
    q: str = "",
    category: str = "",
    include_inactive: bool = False,
) -> list[dict]:
    """q_qualifications を使用件数つきで取得する。

    使用件数 (``use_count``) は確定済み (``status='confirmed'``) cert の数。
    archived/draft は数えない (= マスタを無効化してよいかの判断材料)。
    """
    where: list[str] = []
    params: list = []

    if not include_inactive:
        where.append("ql.is_active = 1")

    q_clean = q.strip()
    if q_clean:
        where.append("(ql.name LIKE ? OR ql.category LIKE ?)")
        like = f"%{q_clean}%"
        params.extend([like, like])

    cat_clean = category.strip()
    if cat_clean:
        where.append("ql.category = ?")
        params.append(cat_clean)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT  ql.qual_id, ql.name, ql.category,
                ql.renewal_required, ql.default_valid_years,
                ql.is_active, ql.display_order,
                ql.created_at, ql.updated_at,
                (SELECT COUNT(*)
                   FROM q_certificates c
                  WHERE c.qual_id = ql.qual_id
                    AND c.status  = 'confirmed') AS use_count
          FROM q_qualifications ql
        {where_sql}
        ORDER BY ql.display_order, ql.name
    """
    cur = await db.execute(sql, params)
    return [dict(r) for r in await cur.fetchall()]


async def _fetch_categories(db) -> list[str]:
    """フィルタドロップダウン用の category 一覧 (空文字を除外)。"""
    cur = await db.execute(
        """
        SELECT DISTINCT category
          FROM q_qualifications
         WHERE category != ''
         ORDER BY category
        """
    )
    return [row[0] for row in await cur.fetchall() if row[0]]


# ────────────────────────────────────────────
# フォームフィールド共通パース
# ────────────────────────────────────────────

def _parse_master_form(form) -> dict:
    """新規/編集の両方で使う POST パース + バリデーション。

    返り値の dict は INSERT/UPDATE のパラメータにそのまま流せる正規化済みの値。
    バリデーション失敗時は HTTPException(400) を投げる。
    """
    name = (form.get("name", "") or "").strip()
    category = (form.get("category", "") or "").strip()
    renewal_required = (form.get("renewal_required", "") == "1")
    is_active = (form.get("is_active", "") == "1")
    valid_years_raw = (form.get("default_valid_years", "") or "").strip()
    display_order_raw = (form.get("display_order", "") or "0").strip()

    if not name:
        raise HTTPException(status_code=400, detail="資格名は必須です")
    if len(name) > 120:
        raise HTTPException(status_code=400, detail="資格名は 120 文字以内で入力してください")

    if valid_years_raw == "":
        default_valid_years: int | None = None
    else:
        try:
            default_valid_years = int(valid_years_raw)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail="有効年数は 0 以上の整数で入力してください",
            ) from e
        if default_valid_years < 0:
            raise HTTPException(
                status_code=400,
                detail="有効年数は 0 以上の整数で入力してください",
            )

    try:
        display_order = int(display_order_raw)
    except ValueError:
        display_order = 0

    return {
        "name": name,
        "category": category,
        "renewal_required": 1 if renewal_required else 0,
        "default_valid_years": default_valid_years,
        "is_active": 1 if is_active else 0,
        "display_order": display_order,
    }


# ────────────────────────────────────────────
# ルート
# ────────────────────────────────────────────

@router.get("/qualifications", response_class=HTMLResponse)
async def qualifications_index(
    request: Request,
    q: str = "",
    category: str = "",
    include_inactive: int = 0,
    user: dict = Depends(_RequireMasterManager),
):
    """資格マスタ一覧。admin のみ。

    クエリパラメータ:
      - ``q``                : 資格名 / カテゴリの部分一致
      - ``category``         : カテゴリ完全一致
      - ``include_inactive`` : 1 なら is_active=0 の行も含める (デフォルト 0)
    """
    include_inactive_b = bool(include_inactive)
    db = await get_db()
    try:
        qualifications = await _fetch_qualifications(
            db, q=q, category=category, include_inactive=include_inactive_b,
        )
        categories = await _fetch_categories(db)
    finally:
        await db.close()

    filters = {
        "q": q.strip(),
        "category": category.strip(),
        "include_inactive": include_inactive_b,
    }
    return _templates.TemplateResponse(
        request,
        "master/qualifications.html",
        {
            "user": user,
            "qualifications": qualifications,
            "categories": categories,
            "filters": filters,
        },
    )


@router.post("/qualifications/new")
async def qualifications_create(
    request: Request,
    user: dict = Depends(_RequireMasterManager),
):
    """新規マスタを作成する。

    name は UNIQUE 制約。重複時は 400 を返す (UI 側で拾ってモーダル内に
    エラー表示する想定)。
    """
    form = await request.form()
    values = _parse_master_form(form)

    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT qual_id FROM q_qualifications WHERE name = ?", (values["name"],),
        )
        if await cur.fetchone() is not None:
            raise HTTPException(
                status_code=400,
                detail=f"既に存在する資格名です: {values['name']}",
            )
        await db.execute(
            """
            INSERT INTO q_qualifications
                (name, category, renewal_required, default_valid_years,
                 is_active, display_order, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))
            """,
            (
                values["name"], values["category"],
                values["renewal_required"], values["default_valid_years"],
                values["is_active"], values["display_order"],
            ),
        )
        await db.commit()
    finally:
        await db.close()

    logger.info(
        "qualifications master 新規追加: name=%s user=%s",
        values["name"], user.get("username"),
    )
    return RedirectResponse(url="/master/qualifications", status_code=303)


@router.post("/qualifications/{qual_id}/edit")
async def qualifications_update(
    request: Request,
    qual_id: int,
    user: dict = Depends(_RequireMasterManager),
):
    """既存マスタを更新する。

    エラーマトリクス:
      - 存在しない qual_id           → 404
      - name が空                    → 400 (parse 段階)
      - 別レコードと name が重複      → 400 (自分自身の name は重複扱いしない)
      - default_valid_years が不正   → 400
    """
    form = await request.form()
    values = _parse_master_form(form)

    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT qual_id FROM q_qualifications WHERE qual_id = ?", (qual_id,),
        )
        if await cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="資格マスタが見つかりません")

        # 名前重複チェック (自分自身は除外する)
        cur = await db.execute(
            "SELECT qual_id FROM q_qualifications "
            "WHERE name = ? AND qual_id != ?",
            (values["name"], qual_id),
        )
        if await cur.fetchone() is not None:
            raise HTTPException(
                status_code=400,
                detail=f"既に存在する資格名です: {values['name']}",
            )

        await db.execute(
            """
            UPDATE q_qualifications
               SET name                = ?,
                   category            = ?,
                   renewal_required    = ?,
                   default_valid_years = ?,
                   is_active           = ?,
                   display_order       = ?,
                   updated_at          = datetime('now','localtime')
             WHERE qual_id = ?
            """,
            (
                values["name"], values["category"],
                values["renewal_required"], values["default_valid_years"],
                values["is_active"], values["display_order"],
                qual_id,
            ),
        )
        await db.commit()
    finally:
        await db.close()

    logger.info(
        "qualifications master 更新: qual_id=%d name=%s user=%s",
        qual_id, values["name"], user.get("username"),
    )
    return RedirectResponse(url="/master/qualifications", status_code=303)
