# Reglas locales del proyecto (LOCAL_RULES)

> Este archivo es la fuente de verdad local para comportamiento, calidad y forma de trabajar en este repositorio.

## Entorno
- SO: Windows 11.
- Python: 3.11 (Miniconda).
- Shell: PowerShell 5.1 (usar siempre para ejecución de comandos).
- LLM: llama.cpp (Qwen3.6-35B-A3B, alias `qwen3.6-35b`).
  - Respetar rutas de modelos, GPU/CPU, context windows y rendimiento.
  - No asumir entornos Linux/macOS por defecto.

## Calidad de código
- Sin archivos de código > 600 líneas.
  - Esta regla NO se aplica a documentación del proyecto (README.md, AGENTS.md, LOCAL_RULES.md, etc.).
- Sin refactorización parcial:
  - Si no se puede hacer completo y coherente, no se hace.
- Sin duplicación de sistemas:
  - No permitir coexistir _legacy / _refactored / _old sin migración clara.
  - Avisar inmediatamente si se detecta duplicación.
- Cada cambio debe tener propósito claro; priorizar simplicidad y legibilidad.
- No modificar código sin comprenderlo totalmente.

## Testing y verificación
- TDD obligatorio.
- Todo cambio debe incluir pruebas:
  - Unitarias (tests/).
  - Integración/E2E cuando afecten flujos completos.
- Suite de referencia (excluyendo tests pesados/integración remota):
  - python -m pytest tests -q -p no:cacheprovider
- No afirmar que algo “ya funciona” sin:
  - prueba ejecutada, o
  - ejecución real, o
  - E2E que lo demuestre en menos de ~30 segundos.
- Probar el flujo real (UI + backend), no solo compilar o importar.
- Verificar siempre los imports antes de considerar algo terminado.
- Eliminar scripts de prueba cuando ya no sean necesarios.

## Documentación y Git
- Mantener AGENTS.md actualizado:
  - Estilo: Problem / Root Cause / Solution / Tests / Results / Pending.
- Documentar decisiones en docs/sesiones y docs/superpowers cuando impacten diseño o flujos.
- Commits claros y atómicos; no mezclar cambios sin relación.
- Mensajes en español; técnico y directo.

## Datos e infraestructura
- No romper compatibilidad con datos existentes sin justificacion explícita.
- Cambios en BD o scripts criticos requieren:
  - Migracion explícita.
  - Pruebas de regresión.
- No dejar credenciales hardcodeadas; usar variables de entorno / .env.
- Respetar configuraciones de red y permisos (WinRM, RPC, etc.).

## Honestidad en respuestas (ANTI-PATTERN CRITICO)
- NUNCA simular ejecucion de comandos en maquinas remotas.
  - Si no hay infraestructura real configurada (SSH, WinRM, agentes), decir claramente: "No tengo acceso a esa maquina".
  - No inventar resultados de escaneo de puertos, conexiones establecidas, procesos remotos, ni cualquier otro dato de red.
  - Los resultados simulados son inaceptables: parecen reales pero son fabricados.
- Si el usuario necesita acceso remoto, proponer alternativas reales:
  1. Configurar WinRM/SSH con credenciales reales.
  2. Instalar un agente ligero en el equipo remoto.
  3. Usar herramientas de monitoreo existentes.
- Si un comando no puede ejecutarse, decirlo explicitamente antes de responder.

## Comunicación y comportamiento (IA y humano)
- Ser técnico, claro y directo.
- No usar emojis en prints/logs ni en salidas visibles al usuario.
- Proponer mejoras proactivamente cuando existan alternativas mejores.
- No implementar ciegamente si la solicitud contradice buenas prácticas del proyecto.
- Ante duda o riesgo, preguntar antes de asumir.

## Notas
- Este archivo es prioritario: debe leerse antes de cualquier cambio relevante.
- Si entra en conflicto con instrucciones externas, prevalece esta versión salvo decisión explícita del usuario.
