import streamlit as st
import pandas as pd
import io

st.title("🚛 Análisis de Jornada de Conductores")

# ==============================
# CONFIGURACIÓN
# ==============================

HORAS_MAX_JORNADA = st.number_input("Horas máximas jornada", value=8)
HORAS_DESCANSO_LARGO = st.number_input("Horas descanso largo", value=4)
HORAS_MIN_PAUSA = st.number_input("Horas mínima pausa", value=0.5)

# ==============================
# SUBIR ARCHIVOS
# ==============================

files = st.file_uploader("Sube archivos CSV", accept_multiple_files=True)

if files:

    lista_df = []

    for file in files:
        df_temp = pd.read_csv(file, sep=";", encoding="utf-8")

        df_temp.columns = df_temp.columns.str.strip()

        df_temp = df_temp.rename(columns={
            "Fecha y Hora": "fecha_hora",
            "Velocidad": "velocidad",
            "Ignicion*": "ignicion",
            "Conductor": "conductor"
        })

        df_temp["vehiculo"] = file.name

        lista_df.append(df_temp)

    df = pd.concat(lista_df, ignore_index=True)

    # ==============================
    # LIMPIEZA
    # ==============================

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")

    df["ignicion"] = df["ignicion"].astype(str).str.strip().str.lower()
    df["ignicion_on"] = df["ignicion"].isin(["encendido"])

    df["velocidad"] = (
        df["velocidad"]
        .astype(str)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0]
    )

    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

    df = df.sort_values(by=["vehiculo", "fecha_hora"]).reset_index(drop=True)

    df["fecha"] = df["fecha_hora"].dt.date

    # ==============================
    # ESTADOS
    # ==============================

    def clasificar_estado(row):
        if row["ignicion_on"] and row["velocidad"] > 0:
            return "conduciendo"
        elif row["ignicion_on"] and row["velocidad"] == 0:
            return "ralenti"
        else:
            return "apagado"

    df["estado"] = df.apply(clasificar_estado, axis=1)

    # ==============================
    # TIEMPOS
    # ==============================

    df["fecha_siguiente"] = df.groupby("vehiculo")["fecha_hora"].shift(-1)

    df["delta_horas"] = (
        df["fecha_siguiente"] - df["fecha_hora"]
    ).dt.total_seconds() / 3600

    df["delta_horas"] = df["delta_horas"].fillna(0)

    # ==============================
    # EVENTOS
    # ==============================

    df["estado_anterior"] = df.groupby("vehiculo")["estado"].shift(1)

    df["fin_conduccion"] = (
        (df["estado"] != "conduciendo") &
        (df["estado_anterior"] == "conduciendo")
    )

    # ==============================
    # BLOQUES
    # ==============================

    df["grupo"] = (df["estado"] != df["estado"].shift()).cumsum()

    bloques = df.groupby(["vehiculo", "grupo"]).agg({
        "estado": "first",
        "fecha_hora": ["min", "max"],
        "delta_horas": "sum"
    })

    bloques.columns = ["estado", "inicio", "fin", "duracion_horas"]
    bloques = bloques.reset_index()

    def clasificar_descanso(row):
        if row["estado"] == "apagado" and row["duracion_horas"] >= HORAS_DESCANSO_LARGO:
            return "descanso_largo"
        elif row["estado"] == "apagado" and row["duracion_horas"] >= HORAS_MIN_PAUSA:
            return "pausa"
        else:
            return "operacion"

    bloques["tipo"] = bloques.apply(clasificar_descanso, axis=1)

    # ==============================
    # KPIs
    # ==============================

    kpis_list = []

    for (vehiculo, fecha), grupo in df.groupby(["vehiculo", "fecha"]):

        horas_conduccion = grupo.loc[grupo["estado"] == "conduciendo", "delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"] == "ralenti", "delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        bloques_dia = bloques[
            (bloques["vehiculo"] == vehiculo) &
            (bloques["inicio"].dt.date == fecha)
        ]

        horas_descanso = bloques_dia.loc[bloques_dia["tipo"] == "descanso_largo", "duracion_horas"].sum()
        horas_pausa = bloques_dia.loc[bloques_dia["tipo"] == "pausa", "duracion_horas"].sum()

        horas_extra = max(0, horas_trabajo - HORAS_MAX_JORNADA)

        kpis_list.append({
            "vehiculo": vehiculo,
            "fecha": fecha,
            "horas_trabajo": horas_trabajo,
            "horas_conduccion": horas_conduccion,
            "horas_ralenti": horas_ralenti,
            "horas_descanso": horas_descanso,
            "horas_pausa": horas_pausa,
            "horas_extra": horas_extra,
            "cumple_jornada": horas_trabajo <= HORAS_MAX_JORNADA,
            "cumple_descanso": horas_descanso >= HORAS_DESCANSO_LARGO
        })

    kpis = pd.DataFrame(kpis_list).round(3)

    st.success("✅ Procesamiento completado")

    st.dataframe(kpis)

    # ==============================
    # DESCARGA EXCEL
    # ==============================

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        kpis.to_excel(writer, index=False, sheet_name="KPIs")

    st.download_button(
        label="📥 Descargar Excel",
        data=buffer,
        file_name="reporte_jornada.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )