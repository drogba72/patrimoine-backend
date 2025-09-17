// app/App.js
import React, { useEffect, useState } from "react";
import { View, Text, TextInput, Button, Alert, StyleSheet } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as LocalAuthentication from "expo-local-authentication";
import * as SecureStore from "expo-secure-store";
import * as WebBrowser from "expo-web-browser";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createStackNavigator } from "@react-navigation/stack";
import { NavigationContainer } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";

// Écrans
import HomeScreen from "./screens/HomeScreen";
import DetailsScreen from "./screens/DetailsScreen";
import AddAssetScreen from "./screens/AddAssetScreen";
import UsersScreen from "./screens/UsersScreen";
import SettingsScreen from "./screens/SettingsScreen";
import LoginScreen from "./screens/LoginScreen";
import RegisterScreen from "./screens/RegisterScreen";
import LoadingScreen from "./screens/LoadingScreen";

WebBrowser.maybeCompleteAuthSession(); // doit s’exécuter une seule fois au boot

const API_BASE = "https://patrimoine-backend-pngw.onrender.com/api";
const Tab = createBottomTabNavigator();
const Stack = createStackNavigator();

function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        tabBarIcon: ({ color, size }) => {
          let iconName;
          if (route.name === "Accueil") iconName = "home-outline";
          else if (route.name === "Répartition") iconName = "pie-chart-outline";
          else if (route.name === "Ajouter") iconName = "add-circle-outline";
          else if (route.name === "Utilisateurs") iconName = "person-outline";
          else if (route.name === "Paramètres") iconName = "settings-outline";
          return <Ionicons name={iconName} size={size} color={color} />;
        },
        tabBarActiveTintColor: "#007AFF",
        tabBarInactiveTintColor: "gray",
      })}
    >
      <Tab.Screen name="Accueil" component={HomeScreen} />
      <Tab.Screen name="Répartition" component={DetailsScreen} />
      <Tab.Screen name="Ajouter" component={AddAssetScreen} />
      <Tab.Screen name="Utilisateurs" component={UsersScreen} />
      <Tab.Screen name="Paramètres" component={SettingsScreen} />
    </Tab.Navigator>
  );
}

function PinScreen({ onSuccess }) {
  const [pin, setPin] = useState("");

  const checkPin = async () => {
    const savedPin = await SecureStore.getItemAsync("app_pin");
    if (pin === savedPin) onSuccess();
    else Alert.alert("Erreur", "Code PIN incorrect");
  };

  return (
    <View style={styles.lockContainer}>
      <Text style={styles.lockTitle}>Entrez votre code PIN</Text>
      <TextInput
        secureTextEntry
        keyboardType="numeric"
        style={styles.lockInput}
        value={pin}
        onChangeText={setPin}
      />
      <Button title="Valider" onPress={checkPin} />
    </View>
  );
}

export default function App() {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState(null);
  const [locked, setLocked] = useState(true);
  const [usePin, setUsePin] = useState(false);

  useEffect(() => {
    const bootstrap = async () => {
      try {
        const token = await AsyncStorage.getItem("token");
        const savedPin = await SecureStore.getItemAsync("app_pin");
        setUsePin(!!savedPin);

        if (token) {
          const res = await fetch(`${API_BASE}/auth/me`, {
            headers: { Authorization: `Bearer ${token}` },
          });
          const j = await res.json();

          if (j.ok) {
            setUser(j);
            if (savedPin) {
              const hasHw = await LocalAuthentication.hasHardwareAsync();
              const enrolled = await LocalAuthentication.isEnrolledAsync();
              if (hasHw && enrolled) {
                const r = await LocalAuthentication.authenticateAsync({
                  promptMessage: "Déverrouiller l'application",
                  fallbackLabel: "Entrer le code",
                });
                if (r.success) setLocked(false);
              } else {
                setLocked(false);
              }
            } else {
              setLocked(false);
            }
          } else {
            setLocked(false);
          }
        } else {
          setLocked(false);
        }
      } catch (e) {
        console.log("Auth init failed", e);
        setLocked(false);
      } finally {
        setLoading(false);
      }
    };
    bootstrap();
  }, []);

  if (loading) return <LoadingScreen />;
  if (locked && usePin) return <PinScreen onSuccess={() => setLocked(false)} />;

  return (
      <Stack.Navigator screenOptions={{ headerShown: false }}>
        {user ? (
          <Stack.Screen name="Main" component={MainTabs} />
        ) : (
          <>
            <Stack.Screen name="Login" component={LoginScreen} />
            <Stack.Screen name="Register" component={RegisterScreen} />
          </>
        )}
      </Stack.Navigator>
  );
}

const styles = StyleSheet.create({
  lockContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    padding: 20,
  },
  lockTitle: { fontSize: 20, fontWeight: "bold", marginBottom: 20 },
  lockInput: {
    borderWidth: 1,
    borderColor: "#ccc",
    width: "60%",
    textAlign: "center",
    fontSize: 22,
    padding: 10,
    marginBottom: 20,
    borderRadius: 8,
  },
});
