import React, { useEffect } from "react";
import { Button, View, Text, Alert } from "react-native";
import * as WebBrowser from "expo-web-browser";
import * as Google from "expo-auth-session/providers/google";
import { makeRedirectUri } from "expo-auth-session";

WebBrowser.maybeCompleteAuthSession();

export default function GoogleLoginTest() {
  const redirectUri = makeRedirectUri({ useProxy: true });

  const [request, response, promptAsync] = Google.useAuthRequest({
    clientId: "368387574548-3iji7mrlna58bolki3bd7qstp9sspg1r.apps.googleusercontent.com",
    scopes: ["profile", "email"],
    redirectUri,
    useProxy: true
  });

  useEffect(() => {
    console.log("📎 Redirect URI utilisé :", redirectUri);
  }, []);

  useEffect(() => {
    console.log("📡 OAuth Response =>", response);

    if (response?.type === "success") {
      const { authentication } = response;
      console.log("✅ Token OAuth reçu :", authentication);
      Alert.alert("Connexion réussie", JSON.stringify(authentication, null, 2));
    } else if (response?.type === "error") {
      console.error("❌ Erreur OAuth :", response.error);
      Alert.alert("Erreur", "Connexion échouée");
    }
  }, [response]);

  return (
    <View style={{ flex: 1, justifyContent: "center", alignItems: "center" }}>
      <Text>Connexion Google Test</Text>
      <Button
        title="Se connecter avec Google"
        onPress={() => {
          console.log("🔁 Lancement promptAsync()");
          promptAsync();
        }}
        disabled={!request}
      />
    </View>
  );
}
