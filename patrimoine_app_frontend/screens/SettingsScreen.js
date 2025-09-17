import React, { useState, useEffect } from "react";
import { View, Text, Switch, StyleSheet, Alert } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";

const API_BASE = "https://patrimoine-backend-pngw.onrender.com/api";

export default function SettingsScreen() {
  const [usePin, setUsePin] = useState(false);
  const [useBiometrics, setUseBiometrics] = useState(false);

  useEffect(() => {
    const loadPreferences = async () => {
      try {
        const localPin = await AsyncStorage.getItem("use_pin");
        const localBiometrics = await AsyncStorage.getItem("use_biometrics");

        if (localPin !== null) setUsePin(localPin === "true");
        if (localBiometrics !== null) setUseBiometrics(localBiometrics === "true");

        const token = await AsyncStorage.getItem("token");
        if (!token) return;

        const res = await fetch(`${API_BASE}/auth/me`, {
          headers: { Authorization: `Bearer ${token}` }
        });

        const j = await res.json();
        if (j.ok) {
          setUsePin(j.user.use_pin);
          setUseBiometrics(j.user.use_biometrics);
          await AsyncStorage.setItem("use_pin", String(j.user.use_pin));
          await AsyncStorage.setItem("use_biometrics", String(j.user.use_biometrics));
        }
      } catch (e) {
        console.log("Erreur chargement préférences :", e);
      }
    };

    loadPreferences();
  }, []);

  const updatePrefs = async (field, value) => {
    const updatedPin = field === "use_pin" ? value : usePin;
    const updatedBiometrics = field === "use_biometrics" ? value : useBiometrics;

    setUsePin(updatedPin);
    setUseBiometrics(updatedBiometrics);

    await AsyncStorage.setItem("use_pin", String(updatedPin));
    await AsyncStorage.setItem("use_biometrics", String(updatedBiometrics));

    const token = await AsyncStorage.getItem("token");
    if (!token) {
      Alert.alert("Erreur", "Non connecté");
      return;
    }

    try {
      const res = await fetch(`${API_BASE}/users/me/security`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({
          use_pin: updatedPin,
          use_biometrics: updatedBiometrics
        })
      });

      const j = await res.json();
      if (!j.ok) Alert.alert("Erreur", j.error || "Impossible de sauvegarder");
    } catch (e) {
      Alert.alert("Erreur réseau", e.message);
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Sécurité</Text>

      <View style={styles.row}>
        <Text style={styles.label}>Activer code PIN</Text>
        <Switch value={usePin} onValueChange={(v) => updatePrefs("use_pin", v)} />
      </View>

      <View style={styles.row}>
        <Text style={styles.label}>Activer biométrie</Text>
        <Switch value={useBiometrics} onValueChange={(v) => updatePrefs("use_biometrics", v)} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 20, backgroundColor: "#fff" },
  title: { fontSize: 20, fontWeight: "700", marginBottom: 20 },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginVertical: 12 },
  label: { fontSize: 16 }
});
