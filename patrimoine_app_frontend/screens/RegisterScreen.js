import React, { useState } from "react";
import { View, Text, TextInput, Button, Alert, StyleSheet } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";

const API_BASE = "https://patrimoine-backend-pngw.onrender.com/api";

export default function RegisterScreen({ navigation }) {
  const [email, setEmail] = useState("");
  const [fullname, setFullname] = useState("");
  const [password, setPassword] = useState("");

  const register = async () => {
    try {
      let res = await fetch(`${API_BASE}/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, fullname, password })
      });
      let j = await res.json();
      if (res.ok) {
        await AsyncStorage.setItem("token", j.token);
        navigation.replace("Main");
      } else {
        Alert.alert("Erreur", j.error || "Impossible de s'inscrire");
      }
    } catch (e) {
      Alert.alert("Erreur réseau", e.message);
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Créer un compte</Text>
      <TextInput style={styles.input} placeholder="Nom complet" value={fullname} onChangeText={setFullname} />
      <TextInput style={styles.input} placeholder="Email" value={email} onChangeText={setEmail} />
      <TextInput style={styles.input} placeholder="Mot de passe" secureTextEntry value={password} onChangeText={setPassword} />
      <Button title="S'inscrire" onPress={register} />
      <Button title="Déjà un compte ?" onPress={() => navigation.navigate("Login")} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex:1, justifyContent:"center", padding:20 },
  title: { fontSize:22, fontWeight:"bold", marginBottom:20 },
  input: { borderWidth:1, borderColor:"#ccc", marginBottom:12, padding:10, borderRadius:6 }
});
