#!/usr/bin/env python3
"""
StreamVault v2 - Harness Engineering Verification Runner
Ejecuta la batería completa de pruebas de regresión, reglas comerciales,
arquitectura limpia y contratos en un entorno temporal aislado en memoria.
"""
import os
import sys
import time
import tempfile
import traceback

def main():
    start_total = time.time()
    print("=" * 65)
    print("🛡️  STREAMVAULT v2 - HARNESS VERIFICATION & QUALITY GATE")
    print("=" * 65)

    # 1. Configurar entorno efímero aislado para pruebas
    temp_dir = tempfile.mkdtemp(prefix="streamvault_v2_harness_")
    os.environ["DATA_DIR"] = temp_dir

    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)

    print(f"📦 Entorno de prueba aislado: {temp_dir}")
    print("⚙️  Inicializando esquema de base de datos v2 en entorno de prueba...")

    try:
        from core.config import settings
        settings.DATA_DIR = temp_dir
        settings.DB_PATH = os.path.join(temp_dir, "services.db")

        import db.schema as schema
        schema.init_db()
        print("✅ Esquema de base de datos v2 inicializado correctamente.\n")
    except Exception as e:
        print(f"❌ Error crítico inicializando el entorno de test v2: {e}")
        traceback.print_exc()
        sys.exit(1)

    # 2. Definir suites de pruebas a ejecutar
    suites = [
        ("Clean Architecture & Modular Contracts", "tests.test_clean_architecture"),
        ("HTTP Custom & HWID Rules", "tests.test_http_custom"),
        ("Tarifas Comerciales y Clientes", "tests.test_pricing_and_clients"),
        ("Aprobación de Pagos y Notificaciones", "tests.test_payments_approval"),
        ("Cuentas, Estados y Vencimientos", "tests.test_accounts_and_alerts"),
        ("Gestión de Grupos y Moderación (Atlas-MD)", "tests.test_groups_and_moderation")
    ]

    failed = 0
    passed = 0

    print("🚀 Ejecutando Batería de Pruebas del Harness v2:")
    print("-" * 65)

    for name, module_path in suites:
        t_start = time.time()
        try:
            mod = __import__(module_path, fromlist=["run_tests"])
            mod.run_tests()
            elapsed = time.time() - t_start
            print(f"  ⏱️  Tiempo: {elapsed:.3f}s\n")
            passed += 1
        except Exception as e:
            elapsed = time.time() - t_start
            print(f"  ❌ FALLO en suite '{name}' ({elapsed:.3f}s): {e}")
            traceback.print_exc()
            print()
            failed += 1

    total_time = time.time() - start_total

    print("=" * 65)
    print("📊 REPORTE DE CALIDAD Y OBSERVABILIDAD DEL HARNESS v2")
    print(f"• Suites Exitosas: {passed}/{len(suites)}")
    print(f"• Suites Fallidas: {failed}/{len(suites)}")
    print(f"• Tiempo Total:   {total_time:.3f} segundos")
    print("=" * 65)

    # Limpieza del entorno efímero
    try:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

    if failed > 0:
        print("🛑 VERIFICACIÓN FALLIDA: Se detectaron regresiones en las reglas de negocio v2.")
        sys.exit(1)
    else:
        print("✨ VERIFICACIÓN EXITOSA: Todas las reglas de negocio, contratos y arquitectura v2 están intactos.")
        sys.exit(0)

if __name__ == "__main__":
    main()
