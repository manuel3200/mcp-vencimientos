import re
import database
from presentation.web.view_models import render_groups_table_rows, render_silent_bans_rows

def run_tests():
    print("  [Suite] Gestión de Grupos, Whitelist y Moderación Avanzada (Atlas-MD)...")

    # ==========================================
    # 1. Pruebas de Modos de Operación del Bot
    # ==========================================
    # Modo inicial predeterminado
    mode_init = database.get_bot_mode()
    assert mode_init in ("public", "private", "self")

    # Cambio a private
    database.set_bot_mode("private")
    assert database.get_bot_mode() == "private"

    # Cambio a self
    database.set_bot_mode("self")
    assert database.get_bot_mode() == "self"

    # Sanitización de modo inválido -> fallback a public
    database.set_bot_mode("invalid_mode_xyz")
    assert database.get_bot_mode() == "public"

    # ==========================================
    # 2. Pruebas de Configuración de Grupos (Whitelist)
    # ==========================================
    group_jid = "12036311223344@g.us"
    g_info = database.upsert_group_config(
        group_jid=group_jid,
        group_name="Comunidad Clientes Streaming",
        bot_enabled=1,
        antilink_enabled=1,
        antilink_action="delete",
        welcome_enabled=1,
        welcome_message="👋 ¡Bienvenido @user al grupo @group!",
        goodbye_enabled=1,
        goodbye_message="👋 Adiós @user"
    )
    assert g_info["group_jid"] == group_jid
    assert g_info["group_name"] == "Comunidad Clientes Streaming"
    assert g_info["bot_enabled"] == 1
    assert g_info["antilink_enabled"] == 1
    assert g_info["welcome_enabled"] == 1

    # Verificación de permisos de respuesta del bot
    assert database.is_group_bot_enabled(group_jid) is True
    
    # Deshabilitar bot en este grupo (ignorado)
    database.set_group_bot_enabled(group_jid, False)
    assert database.is_group_bot_enabled(group_jid) is False

    # Volver a habilitar bot
    database.set_group_bot_enabled(group_jid, True)
    assert database.is_group_bot_enabled(group_jid) is True

    # Grupo desconocido no registrado -> por defecto False
    assert database.is_group_bot_enabled("unknown_group_999@g.us") is False

    # Verificación de Antilink
    is_anti, act = database.is_group_antilink_enabled(group_jid)
    assert is_anti is True
    assert act == "delete"

    # Modificar acción de antilink a kick (expulsión)
    database.set_group_antilink(group_jid, enabled=True, action="kick")
    is_anti_kick, act_kick = database.is_group_antilink_enabled(group_jid)
    assert is_anti_kick is True
    assert act_kick == "kick"

    # Desactivar antilink
    database.set_group_antilink(group_jid, enabled=False)
    is_anti_off, _ = database.is_group_antilink_enabled(group_jid)
    assert is_anti_off is False

    # Listar grupos
    all_groups = database.list_groups_config()
    assert len(all_groups) >= 1
    assert any(g["group_jid"] == group_jid for g in all_groups)

    # ==========================================
    # 3. Pruebas de Baneo Silencioso (Silent Ban - Atlas-MD)
    # ==========================================
    banned_phone = "5491199887766"
    assert database.is_silent_banned(banned_phone) is False

    # Aplicar baneo a usuario
    ok_ban = database.add_silent_ban(banned_phone, reason="Spam recurrente en chats")
    assert ok_ban is True
    assert database.is_silent_banned(banned_phone) is True

    # Comprobación resiliente con formato internacional o espacios
    assert database.is_silent_banned("+54 9 11 9988-7766") is True
    assert database.is_silent_banned("1199887766") is True

    # Aplicar baneo a grupo tóxico (@g.us)
    spam_group_jid = "120363999888@g.us"
    ok_gban = database.add_silent_ban(spam_group_jid, reason="Grupo de publicidad masiva")
    assert ok_gban is True
    assert database.is_silent_banned(spam_group_jid) is True

    # Usuario no silenciado
    assert database.is_silent_banned("5491100001111") is False

    # Listar baneos
    bans = database.list_silent_bans()
    assert len(bans) >= 2
    assert any(b["target_id"] == banned_phone for b in bans)
    assert any(b["target_id"] == spam_group_jid for b in bans)

    # Desbanear usuario y grupo
    assert database.remove_silent_ban(banned_phone) is True
    assert database.is_silent_banned(banned_phone) is False

    assert database.remove_silent_ban(spam_group_jid) is True
    assert database.is_silent_banned(spam_group_jid) is False

    # ==========================================
    # 4. Pruebas de Detección Regex de Enlaces (Antilink)
    # ==========================================
    def detect_forbidden_link(message: str) -> bool:
        link_patterns = [
            r'chat\.whatsapp\.com\/[A-Za-z0-9]{15,}',
            r'wa\.me\/[0-9]+',
            r'https?:\/\/[^\s]+',
            r't\.me\/[A-Za-z0-9_]+'
        ]
        return any(re.search(p, message, re.IGNORECASE) for p in link_patterns)

    assert detect_forbidden_link("Únanse a mi grupo https://chat.whatsapp.com/AbCdEfGhIjKlMnOp") is True
    assert detect_forbidden_link("Miren esta oferta http://sitio-peligroso.xyz/promo") is True
    assert detect_forbidden_link("Escríbeme a wa.me/54911223344") is True
    assert detect_forbidden_link("Canal telegram t.me/canalofertas") is True
    assert detect_forbidden_link("Hola buenas tardes, ¿tienen stock de Netflix?") is False
    assert detect_forbidden_link("Mi usuario es cliente@gmail.com y clave 12345") is False

    # ==========================================
    # 5. Pruebas de View-Models HTML (Panel Web)
    # ==========================================
    # Renderizado de tabla de grupos
    html_groups = render_groups_table_rows([g_info])
    assert "Comunidad Clientes Streaming" in html_groups
    assert group_jid in html_groups
    assert 'toggleGroupBot' in html_groups
    assert 'toggleGroupAntilink' in html_groups
    assert 'toggleGroupWelcome' in html_groups

    # Renderizado vacío de grupos
    html_empty_groups = render_groups_table_rows([])
    assert "No hay grupos registrados o sincronizados aún" in html_empty_groups

    # Renderizado de tabla de baneos silenciosos
    sample_ban = {
        "target_id": "5491122334455",
        "target_type": "user",
        "reason": "Intento de fraude",
        "created_at": "2026-09-20 02:00:00"
    }
    html_bans = render_silent_bans_rows([sample_ban])
    assert "5491122334455" in html_bans
    assert "Intento de fraude" in html_bans
    assert "removeSilentBan" in html_bans

    # Renderizado vacío de baneos
    html_empty_bans = render_silent_bans_rows([])
    assert "No hay números ni grupos con baneo silencioso" in html_empty_bans

    print("  ✅ [Suite] Gestión de Grupos, Whitelist y Moderación completada con éxito.")
