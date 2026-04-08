import streamlit as st
import pandas as pd
import io
import re

st.title("🚛 Análisis de Jornada de Conductores")

# ==============================
# CONFIGURACIÓN
# ==============================

HORAS_MAX_JORNADA = st.number_input("Horas máximas jornada", value=8.0)
HORAS_DESCANSO_LARGO = st.number_input("Horas descanso largo", value=4.0)
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
        "fecha_hora": ["min", "max"]
    })

    bloques.columns = ["estado", "inicio", "fin"]
    bloques = bloques.reset_index()

    # ==============================
    # 🔥 CORTE DE BLOQUES POR DÍA
    # ==============================

    filas = []

    for _, row in bloques.iterrows():

        inicio = row["inicio"]
        fin = row["fin"]

        actual = inicio

        while actual.date() <= fin.date():

            fin_dia = pd.Timestamp.combine(actual.date(), pd.Timestamp.max.time())

            corte_fin = min(fin, fin_dia)

            horas = (corte_fin - actual).total_seconds() / 3600

            filas.append({
                "vehiculo": row["vehiculo"],
                "estado": row["estado"],
                "fecha": actual.date(),
                "duracion_horas": horas
            })

            actual = corte_fin + pd.Timedelta(seconds=1)

    bloques_dia = pd.DataFrame(filas)

    # Clasificar descanso
    def clasificar_descanso(row):
        if row["estado"] == "apagado" and row["duracion_horas"] >= HORAS_DESCANSO_LARGO:
            return "descanso_largo"
        elif row["estado"] == "apagado" and row["duracion_horas"] >= HORAS_MIN_PAUSA:
            return "pausa"
        else:
            return "operacion"

    bloques_dia["tipo"] = bloques_dia.apply(clasificar_descanso, axis=1)

    # ==============================
    # KPIs
    # ==============================

    kpis_list = []

    for (vehiculo, fecha), grupo in df.groupby(["vehiculo", "fecha"]):

        conductor = grupo["conductor"].dropna().iloc[0] if "conductor" in grupo else "N/A"

        inicio_jornada = grupo.loc[grupo["ignicion_on"], "fecha_hora"].min()
        fin_jornada = grupo.loc[grupo["ignicion_on"], "fecha_hora"].max()

        horas_conduccion = grupo.loc[grupo["estado"] == "conduciendo", "delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"] == "ralenti", "delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        numero_paradas = grupo["fin_conduccion"].sum()

        bloques_filtrados = bloques_dia[
            (bloques_dia["vehiculo"] == vehiculo) &
            (bloques_dia["fecha"] == fecha)
        ]

        horas_descanso = bloques_filtrados.loc[bloques_filtrados["tipo"] == "descanso_largo", "duracion_horas"].sum()
        horas_pausa = bloques_filtrados.loc[bloques_filtrados["tipo"] == "pausa", "duracion_horas"].sum()

        horas_extra = max(0, horas_trabajo - HORAS_MAX_JORNADA)

        kpis_list.append({
            "conductor": conductor,
            "vehiculo": vehiculo,
            "fecha": fecha,
            "inicio_jornada": inicio_jornada,
            "fin_jornada": fin_jornada,
            "numero_paradas": numero_paradas,
            "horas_trabajo": horas_trabajo,
            "horas_conduccion": horas_conduccion,
            "horas_ralenti": horas_ralenti,
            "horas_descanso": horas_descanso,
            "horas_pausa": horas_pausa,
            "horas_extra": horas_extra
        })

    kpis = pd.DataFrame(kpis_list).round(2)
    kpis = kpis.sort_values(by=["conductor", "fecha"])

    st.subheader("📊 Resumen por conductor")
    st.dataframe(kpis)

    # ==============================
    # EXPORTAR EXCEL
    # ==============================

    def limpiar_nombre(nombre):
        if pd.isna(nombre):
            return "SinNombre"
        return re.sub(r'[\\/*?:\\[\\]]', "", str(nombre))[:31]

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:

        for conductor, df_conductor in kpis.groupby("conductor"):

            nombre_hoja = limpiar_nombre(conductor)

            df_conductor.to_excel(writer, sheet_name=nombre_hoja, index=False)

            ws = writer.sheets[nombre_hoja]

            for i, col in enumerate(df_conductor.columns):
                try:
                    max_len = max(
                        df_conductor[col].astype(str).apply(len).max(),
                        len(col)
                    )
                except:
                    max_len = len(col)

                ws.column_dimensions[chr(65 + i)].width = max_len + 2

            # BLOQUES POR DÍA
            bloques_cond = bloques_dia[
                bloques_dia["vehiculo"].isin(df_conductor["vehiculo"])
            ]

            nombre_bloques = limpiar_nombre(f"Bloques {conductor}")

            bloques_cond.to_excel(writer, sheet_name=nombre_bloques, index=False)

    st.download_button(
        label="📥 Descargar reporte por conductor",
        data=buffer,
        file_name="reporte_jornada_conductores.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )