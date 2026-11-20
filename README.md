# Buscador de Operación FRM · Garantías

App web para el equipo de cobro de garantías de Grupo OneSummit Post Venta.
Se ingresa el número de parte del repuesto causante, la app busca el nombre del repuesto en internet (Serper / Google)
y lo cruza con el catálogo FRM Pasajeros (2.522 operaciones) para sugerir el código de operación del reclamo.

- `frm.json`: catálogo FRM (desde `INDICE FRM PASAJEROS.xlsx`, hoja BD FRM PASAJEROS).
- Los nombres encontrados se guardan por los 5 primeros dígitos del número de parte (mismo código = mismo repuesto) para ahorrar búsquedas.
- Acceso con usuario y clave. El administrador crea las cuentas en el panel "Usuarios".
- Las cuentas se guardan en `usuarios.json` en este repo (claves encriptadas), vía la API de GitHub.
- Variables de entorno en Render:
  - `SERPER_API_KEY`: clave de serper.dev (búsqueda en internet).
  - `ADMIN_USER` / `ADMIN_PASSWORD`: cuenta del administrador.
  - `GITHUB_TOKEN`: token de GitHub con permiso de escritura en este repo (para guardar usuarios).
  - `SECRET_KEY`: la genera Render automáticamente.

Para actualizar el catálogo: `python build_data.py "INDICE FRM PASAJEROS.xlsx"`.
