// screens/LoginScreen.js
import React, { useEffect, useState, useCallback } from "react";
import { View, Text, TextInput, Alert, StyleSheet, TouchableOpacity, Image } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as Google from "expo-auth-session/providers/google";
import * as AuthSession from "expo-auth-session";

const API_BASE = "https://patrimoine-backend-pngw.onrender.com/api";

// Ton client OAuth **Android** (Google Cloud > Identifiants > Client Android)
const ANDROID_CLIENT_ID = "368387574548-66jjoi3ukdej1f0d90o57as0rfrtlo9l.apps.googleusercontent.com";

// Découverte Google (nécessaire pour l’échange code -> token)
const googleDiscovery = {
  authorizationEndpoint: "https://accounts.google.com/o/oauth2/v2/auth",
  tokenEndpoint: "https://oauth2.googleapis.com/token",
};

export default function LoginScreen({ navigation }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  // ❌ PAS de redirectUri ici — on laisse le provider utiliser celui attendu par Google
  // ❌ PAS de responseType: "token" — on fait le "code + PKCE" (par défaut)
  const [request, response, promptAsync] = Google.useAuthRequest({
    androidClientId: ANDROID_CLIENT_ID,
    scopes: ["openid", "profile", "email"],
    useProxy: false, // dev client natif
    // usePKCE: true  // (true par défaut)
  });

  useEffect(() => {
    console.log("🔗 request.redirectUri =", request?.redirectUri);
  }, [request]);

  useEffect(() => {
    console.log("📡 OAuth Response =>", response);
    if (!response) return;

    (async () => {
      if (response.type === "success") {
        const code = response.params?.code;
        const codeVerifier = request?.codeVerifier;
        const redirectUri = request?.redirectUri;

        if (!code || !codeVerifier || !redirectUri) {
          Alert.alert("Google", "Réponse incomplète (code/verifier/redirectUri).");
          return;
        }

        // Échange du code contre un access_token (pas de client secret en PKCE)
        const tokenRes = await AuthSession.exchangeCodeAsync(
          {
            clientId: ANDROID_CLIENT_ID,
            code,
            redirectUri,
            extraParams: { code_verifier: codeVerifier },
          },
          googleDiscovery
        );

        const accessToken = tokenRes.accessToken || tokenRes.access_token;
        console.log("✅ accessToken =", accessToken ? accessToken.slice(0, 8) + "..." : "<none>");

        if (accessToken) {
          await handleGoogleLogin(accessToken);
        } else {
          Alert.alert("Google", "Impossible d’obtenir un access_token");
        }
      } else if (response.type === "error") {
        console.error("❌ Erreur OAuth Google :", response.error);
        Alert.alert("Erreur Google", "Échec de l'authentification Google");
      } else if (response.type === "dismiss") {
        console.warn("⚠️ Connexion Google annulée/fermée.");
      }
    })();
  }, [response, request]);

  const handleGoogleLogin = useCallback(async (accessToken) => {
    try {
      console.log("📤 POST backend avec token :", accessToken.slice(0, 8) + "...");
      const res = await fetch(`${API_BASE}/auth/google`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_token: accessToken }),
      });
      const j = await res.json().catch(() => ({}));
      console.log("📥 Réponse backend :", res.status, j);
      if (res.ok && j.token) {
        await AsyncStorage.setItem("token", j.token);
        navigation.replace("Main");
      } else {
        Alert.alert("Erreur backend", j.error || `Status ${res.status}`);
      }
    } catch (e) {
      console.error("💥 Erreur backend :", e);
      Alert.alert("Erreur réseau", e.message || "Impossible de joindre le serveur");
    }
  }, [navigation]);

  const login = async () => {
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const j = await res.json();
      console.log("📥 /auth/login :", res.status, j);
      if (res.ok && j.token) {
        await AsyncStorage.setItem("token", j.token);
        navigation.replace("Main");
      } else {
        Alert.alert("Erreur", j.error || "Email ou mot de passe incorrect");
      }
    } catch (e) {
      Alert.alert("Erreur réseau", e.message);
    }
  };

  const onGooglePress = useCallback(async () => {
    try {
      console.log("🔁 promptAsync()…");
      const result = await promptAsync(); // pas de { useProxy:true }
      console.log("🔙 Résultat promptAsync() =>", result);
    } catch (e) {
      console.error("💥 promptAsync exception :", e);
    }
  }, [promptAsync]);

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Connexion</Text>

      <TextInput style={styles.input} placeholder="Email"
        value={email} autoCapitalize="none" keyboardType="email-address"
        onChangeText={setEmail} />
      <TextInput style={styles.input} placeholder="Mot de passe"
        secureTextEntry value={password} onChangeText={setPassword} />

      <TouchableOpacity style={styles.button} onPress={login}>
        <Text style={styles.buttonText}>SE CONNECTER</Text>
      </TouchableOpacity>

      <View style={styles.separator} />

      <TouchableOpacity style={styles.googleButton} onPress={onGooglePress} disabled={!request}>
        <Image
          source={{ uri: "https://upload.wikimedia.org/wikipedia/commons/thumb/5/53/Google_%22G%22_Logo.svg/512px-Google_%22G%22_Logo.svg.png" }}
          style={styles.googleIcon}
        />
        <Text style={styles.googleText}>Se connecter avec Google</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container:{flex:1,justifyContent:"center",padding:20,backgroundColor:"#fff"},
  title:{fontSize:22,fontWeight:"bold",marginBottom:20},
  input:{borderWidth:1,borderColor:"#ccc",marginBottom:12,padding:10,borderRadius:6},
  button:{backgroundColor:"#007AFF",padding:14,borderRadius:6,marginTop:10},
  buttonText:{color:"#fff",textAlign:"center",fontWeight:"bold"},
  separator:{marginVertical:30,height:1,backgroundColor:"#eee"},
  googleButton:{flexDirection:"row",alignItems:"center",backgroundColor:"#fff",borderColor:"#ccc",borderWidth:1,borderRadius:6,padding:12},
  googleIcon:{width:20,height:20,marginRight:10},
  googleText:{fontSize:16,color:"#333"},
});
