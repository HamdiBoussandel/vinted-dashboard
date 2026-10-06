import json
import asyncio

import logging
logger = logging.getLogger(__name__)


async def _resolve(cdp, node_id):
    resolved = await cdp.send("DOM.resolveNode", {"nodeId": node_id})
    return resolved["object"]["objectId"]


def walk_nodes(node):
    """Parcourt récursivement l'arbre DOM (pierce=True) et retourne les éléments avec un id."""
    results = []
    attrs_list = node.get("attributes", [])
    attrs = {}
    for i in range(0, len(attrs_list), 2):
        attrs[attrs_list[i]] = attrs_list[i + 1]
    if "id" in attrs:
        results.append({"id": attrs["id"], "node_id": node.get("nodeId"), "attrs": attrs})
    for sr in node.get("shadowRoots", []) or []:
        results += walk_nodes(sr)
    for child in node.get("children", []) or []:
        results += walk_nodes(child)
    if node.get("contentDocument"):
        results += walk_nodes(node["contentDocument"])
    return results


async def snapshot_by_id(cdp):
    """Retourne un dict {id: {node_id, attrs}} de tous les éléments visibles via CDP (pierce shadow + iframes)."""
    doc = await cdp.send("DOM.getDocument", {"pierce": True, "depth": -1})
    nodes = walk_nodes(doc["root"])
    by_id = {}
    for n in nodes:
        by_id.setdefault(n["id"], n)
    return by_id


async def get_live_props(cdp, node_id, props=("checked", "value", "className")):
    object_id = await _resolve(cdp, node_id)
    prop_list = ", ".join(f'"{p}": this.{p}' for p in props)
    result = await cdp.send("Runtime.callFunctionOn", {
        "objectId": object_id,
        "functionDeclaration": f"function() {{ return JSON.stringify({{{prop_list}}}); }}",
        "returnByValue": True,
    })
    return json.loads(result["result"]["value"])


async def click_element(cdp, node_id):
    object_id = await _resolve(cdp, node_id)
    await cdp.send("Runtime.callFunctionOn", {
        "objectId": object_id,
        "functionDeclaration": "function() { this.click(); }",
    })


async def set_radio_checked(cdp, node_id):
    object_id = await _resolve(cdp, node_id)
    await cdp.send("Runtime.callFunctionOn", {
        "objectId": object_id,
        "functionDeclaration": """
            function() {
                this.checked = true;
                this.dispatchEvent(new Event('input', { bubbles: true }));
                this.dispatchEvent(new Event('change', { bubbles: true }));
                this.click();
            }
        """,
    })


async def set_input_value(cdp, node_id, value):
    """Pour les champs texte/nombre (ex: itemsBegin, searchWordForm input)."""
    object_id = await _resolve(cdp, node_id)
    await cdp.send("Runtime.callFunctionOn", {
        "objectId": object_id,
        "functionDeclaration": """
            function(value) {
                const setter = Object.getOwnPropertyDescriptor(this.__proto__, 'value').set;
                setter.call(this, value);
                this.dispatchEvent(new Event('input', { bubbles: true }));
                this.dispatchEvent(new Event('change', { bubbles: true }));
            }
        """,
        "arguments": [{"value": value}],
    })


async def query_selector_in(cdp, parent_node_id, selector):
    """Cherche un sélecteur CSS classique (tag/classe/attribut) DANS le sous-arbre d'un nœud donné.
    Utile pour les enfants sans id propre (ex: l'input et le bouton dans #searchWordForm)."""
    result = await cdp.send("DOM.querySelector", {"nodeId": parent_node_id, "selector": selector})
    node_id = result.get("nodeId")
    return node_id if node_id else None


async def get_center_coords(cdp, node_id):
    """Retourne les coordonnées écran (x, y) du centre d'un élément via getBoundingClientRect (JS),
    plus fiable que DOM.getBoxModel qui échoue si le layout n'est pas encore calculé."""
    object_id = await _resolve(cdp, node_id)
    result = await cdp.send("Runtime.callFunctionOn", {
        "objectId": object_id,
        "functionDeclaration": """
            function() {
                const rect = this.getBoundingClientRect();
                return JSON.stringify({
                    x: rect.x + rect.width / 2,
                    y: rect.y + rect.height / 2,
                    width: rect.width,
                    height: rect.height,
                });
            }
        """,
        "returnByValue": True,
    })
    data = json.loads(result["result"]["value"])
    return data["x"], data["y"]


async def real_mouse_click(page, x, y, hold_ms=80):
    """Clic souris réaliste (mousedown -> pause -> mouseup), nécessaire pour les éléments
    draggables/écouteurs bas-niveau qui n'écoutent pas 'click' mais mousedown/mouseup
    (ex: #miniVinz, les cartes produit de la grille Vinted en mode sélection Clemz)."""
    await page.mouse.move(x, y)
    await page.mouse.down()
    await asyncio.sleep(hold_ms / 1000)
    await page.mouse.up()


async def is_element_visible(cdp, node_id):
    """Retourne True si l'élément est RÉELLEMENT rendu à l'écran — pas seulement si son propre
    style.display est différent de 'none'. Un ancêtre masqué (display:none, visibility:hidden,
    ou dimensions nulles suite à une transition d'état) rend l'élément invisible même si son
    propre display reste 'block' — d'où l'usage de getBoundingClientRect qui reflète le rendu
    réel en tenant compte de toute la chaîne de parents."""
    object_id = await _resolve(cdp, node_id)
    result = await cdp.send("Runtime.callFunctionOn", {
        "objectId": object_id,
        "functionDeclaration": """
            function() {
                const style = getComputedStyle(this);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = this.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            }
        """,
        "returnByValue": True,
    })
    return result["result"]["value"]


async def dismiss_sync_modal(cdp):
    """Ferme la modale périodique 'Synchronisation des ventes' (#syncSalesModal) si présente,
    en cliquant sur son bouton de fermeture ('Plus tard' / croix)."""
    by_id = await snapshot_by_id(cdp)
    if "syncSalesModal" not in by_id:
        return False

    close_node_id = await query_selector_in(
        cdp, by_id["syncSalesModal"]["node_id"], ".close, .btn-secondary"
    )
    if close_node_id:
        await click_element(cdp, close_node_id)
        await asyncio.sleep(0.3)
        return True
    return False


async def ensure_panel_open(cdp, page):
    """S'assure que le panneau Clemz (#toolBody) est visible et sans modale bloquante :
    - ferme #syncSalesModal si présente
    - si masqué, effectue un vrai clic sur #miniVinz pour rouvrir (idempotent, ne clique
      pas s'il est déjà visible, évite de le refermer par erreur puisque #miniVinz est un toggle,
      et la session étant persistante d'un run à l'autre).

    CORRECTIF : revérifie RÉELLEMENT la visibilité après le clic au lieu de supposer le succès
    -- l'ancienne version retournait toujours True après le clic, masquant silencieusement un
    panneau resté fermé (observé : le clic peut ne pas ouvrir le panneau pour une raison encore
    inconnue, et tout le flux qui suivait s'exécutait alors sur un panneau invisible, ignoré en
    interne par Clemz). Une seconde tentative de clic est faite si la première ne suffit pas,
    au cas où ce soit simplement un rendu plus lent qu'attendu (mode "très lent" de Clemz)."""
    await dismiss_sync_modal(cdp)

    by_id = await snapshot_by_id(cdp)
    if "toolBody" not in by_id or "miniVinz" not in by_id:
        return False

    visible = await is_element_visible(cdp, by_id["toolBody"]["node_id"])
    if visible:
        return True

    x, y = await get_center_coords(cdp, by_id["miniVinz"]["node_id"])
    await real_mouse_click(page, x, y)
    await asyncio.sleep(0.8)
    await dismiss_sync_modal(cdp)

    by_id = await snapshot_by_id(cdp)
    if "toolBody" not in by_id:
        return False
    visible = await is_element_visible(cdp, by_id["toolBody"]["node_id"])

    if not visible:
        logger.warning("⚠️ [ensure_panel_open] Panneau toujours fermé après 1er clic — nouvelle tentative...")
        await asyncio.sleep(1.0)
        x, y = await get_center_coords(cdp, by_id["miniVinz"]["node_id"])
        await real_mouse_click(page, x, y)
        await asyncio.sleep(1.2)
        await dismiss_sync_modal(cdp)

        by_id = await snapshot_by_id(cdp)
        if "toolBody" not in by_id:
            return False
        visible = await is_element_visible(cdp, by_id["toolBody"]["node_id"])

    return visible

async def force_panel_visible(cdp):
    """Force #toolBody à display:block directement via CDP, sans passer par un clic.
    À utiliser uniquement APRÈS que le panneau a déjà été ouvert une première fois via un vrai
    clic sur #miniVinz (ce qui initialise correctement l'état interne de Clemz) — ce forçage
    sert seulement à récupérer la visibilité si un état interne ('Réduire', etc.) l'a masquée,
    sans risquer de la refermer par un toggle mal interprété."""
    by_id = await snapshot_by_id(cdp)
    if "toolBody" not in by_id:
        return False
    object_id = await _resolve(cdp, by_id["toolBody"]["node_id"])
    await cdp.send("Runtime.callFunctionOn", {
        "objectId": object_id,
        "functionDeclaration": """
            function() {
                this.style.setProperty('display', 'block', 'important');
            }
        """,
    })
    return True

async def set_select_value(cdp, node_id, value):
    """Pour les <select> (ex: modifyPriceDirection, modifyPriceType, modifyPriceRound).
    Un <select> natif n'a pas besoin du contournement de setter utilisé par
    set_input_value (celui-ci sert à tromper les frameworks type React) : assigner
    directement .value suffit, suivi d'un 'change' pour notifier les listeners Clemz."""
    object_id = await _resolve(cdp, node_id)
    await cdp.send("Runtime.callFunctionOn", {
        "objectId": object_id,
        "functionDeclaration": """
            function(value) {
                this.value = value;
                this.dispatchEvent(new Event('change', { bubbles: true }));
            }
        """,
        "arguments": [{"value": value}],
    })