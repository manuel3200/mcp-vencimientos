#!/usr/bin/env python3
"""
StreamVault - Test Harness Verification Runner
Ejecuta la batería de pruebas de regresión y reglas de negocio sobre un entorno
aislado en memoria/temporal sin tocar la base de datos de producción.
"""
import os
import sys
import time
import tempfile
import traceback

def main():
    start_total = time.time()
    print("=" * 65)
    print("🛡️  STREAMVAULT - HARNESS ENGINEERING VERIFICATION RUNNER")
    print("=" * 65)

    # 1. Configurar directorio aislado para tests (Base de datos efímera)
    temp_dir = tempfile.mkdtemp(prefix="streamvault_harness_")
    os.environ["DATA_DIR"] = temp_dir

    # Asegurar que el directorio de v1 esté en el path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)

    print(f"📦 Entorno de prueba aislado: {temp_dir}")
    print("⚙️  Inicializando esquema de base de datos en entorno de prueba...")

    try:
        from core.config import settings
        settings.DATA_DIR = temp_dir
        settings.DB_PATH = os.path.join(temp_dir, "services.db")

        import db.schema as schema
        schema.init_db()
        print("✅ Esquema de base de datos inicializado correctamente.\n")
    except Exception as e:
        print(f"❌ Error crítico inicializando el entorno de test: {e}")
        traceback.print_exc()
        sys.exit(1)

    # 2. Definir suites de pruebas a ejecutar
    suites = [
        ("HTTP Custom & HWID Rules", "tests.test_http_custom"),
        ("Tarifas Comerciales y Clientes", "tests.test_pricing_and_clients"),
        ("Aprobación de Pagos y Notificaciones", "tests.test_payments_approval"),
        ("Cuentas, Estados y Vencimientos", "tests.test_accounts_and_alerts")
    ]

    failed = 0
    passed = 0

    print("🚀 Ejecutando Batería de Pruebas del Harness:")
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
    print("📊 REPORTE DE CALIDAD Y OBSERVABILIDAD DEL HARNESS")
    print(f"• Suites Exitosas: {passed}/{len(suites)}")
    print(f"• Suites Fallidas: {failed}/{len(suites)}")
    print(f"• Tiempo Total:   {total_time:.3f} segundos")
    print("=" * 65)

    # Limpieza del directorio temporal
    try:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

    if failed > 0:
        print("🛑 VERIFICACIÓN FALLIDA: Se detectaron regresiones en las reglas de negocio.")
        sys.exit(1)
    else:
        print("✨ VERIFICACIÓN EXITOSA: Todas las reglas de negocio y contratos están intactos.")
        sys.exit(0)

if __name__ == "__main__":
    main()
